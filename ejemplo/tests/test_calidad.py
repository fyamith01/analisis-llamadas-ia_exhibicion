"""Contratos de calidad entre capas: cada regla detecta su defecto y una violación impide publicar la capa."""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from apoyo import leer_capa
from test_pipeline import TEXTO_LARGO, crear_fuente

import calidad
import generar_datos
import pipeline

PERIODO = "2026-10"


@pytest.fixture(scope="module")
def capas(corrida: Path) -> dict[str, pd.DataFrame]:
    hechos = leer_capa(corrida / "negocio" / "modelo" / "hecho_evaluacion")
    return {
        "crudo": pd.read_parquet(corrida / pipeline.ruta_capa("crudo", PERIODO).relative_to(pipeline.RAIZ)),
        "enriquecido": pd.read_parquet(corrida / pipeline.ruta_capa("enriquecido", PERIODO).relative_to(pipeline.RAIZ)),
        "evaluaciones": leer_capa(corrida / "negocio" / "evaluaciones"),
        "hechos": hechos,
    }


def evaluar(capa: str, t: dict) -> dict[str, int]:
    """Aplica el contrato de `capa` sobre las tablas de `t` (ya corrompidas o no)."""
    if capa == "crudo":
        df = t["crudo"]
        embudo = t.get("embudo") or {"universo_voz": int((df["canal"] == "voz").sum()) + 5,
                                     "voz": int((df["canal"] == "voz").sum()),
                                     "chats": int((df["canal"] == "whatsapp").sum())}
        return calidad.contrato_crudo(df, PERIODO, embudo, pipeline.MIN_CARACTERES)
    if capa == "enriquecido":
        return calidad.contrato_enriquecido(t["enriquecido"], t["crudo"]["conversacion_id"], pipeline.PESOS,
                                            pipeline.MOTIVOS, pipeline.version_analisis())
    if capa == "negocio":
        evaluaciones = t["evaluaciones"][t["evaluaciones"]["periodo"] == PERIODO]
        return calidad.contrato_negocio(evaluaciones, t["enriquecido"], t.get("pesos", pipeline.PESOS))
    return calidad.contrato_modelo(t["hechos"], t["evaluaciones"], len(pipeline.PESOS))


def fila(df: pd.DataFrame, columna: str, valor: object, donde: pd.Series | None = None) -> pd.DataFrame:
    """Copia de `df` con `columna` = `valor` en la primera fila (o la primera que cumpla `donde`)."""
    df = df.copy()
    indice = df.index[0] if donde is None else df.index[donde(df)][0]
    df[columna] = df[columna].astype(object)
    df.loc[indice, columna] = valor
    return df


def analizable(df: pd.DataFrame) -> pd.Series:
    return df["analizable"]


def omitida(df: pd.DataFrame) -> pd.Series:
    return ~df["analizable"]


def embudo_de(df: pd.DataFrame, **cambios: int) -> dict[str, int]:
    voz = int((df["canal"] == "voz").sum())
    return {"universo_voz": voz, "voz": voz, "chats": int((df["canal"] == "whatsapp").sum()), **cambios}


CORRUPCIONES: list[tuple[str, str, Callable[[dict], dict]]] = [
    ("crudo", "conversacion_id único y no nulo",
     lambda t: {**t, "crudo": fila(t["crudo"], "conversacion_id", t["crudo"]["conversacion_id"].iloc[1])}),
    ("crudo", "campos obligatorios sin nulos", lambda t: {**t, "crudo": fila(t["crudo"], "agente_id", None)}),
    ("crudo", "canal dentro del catálogo", lambda t: {**t, "crudo": fila(t["crudo"], "canal", "correo")}),
    ("crudo", "fecha dentro del periodo",
     lambda t: {**t, "crudo": fila(t["crudo"], "fecha", datetime(2026, 9, 30, 23, 59))}),
    ("crudo", "filas = voz seleccionada + chats",
     lambda t: {**t, "embudo": embudo_de(t["crudo"], chats=int((t["crudo"]["canal"] == "whatsapp").sum()) + 1)}),
    ("crudo", "voz seleccionada <= universo de voz",
     lambda t: {**t, "embudo": embudo_de(t["crudo"], universo_voz=int((t["crudo"]["canal"] == "voz").sum()) - 1)}),
    ("crudo", "analizable coherente con el largo del texto",
     lambda t: {**t, "crudo": fila(t["crudo"], "analizable", False, analizable)}),
    ("enriquecido", "mismas conversaciones que la capa cruda",
     lambda t: {**t, "enriquecido": t["enriquecido"].iloc[1:]}),
    ("enriquecido", "notas de 1 a 10 en las analizables",
     lambda t: {**t, "enriquecido": fila(t["enriquecido"], "solucion", 11, analizable)}),
    ("enriquecido", "motivo dentro del catálogo",
     lambda t: {**t, "enriquecido": fila(t["enriquecido"], "motivo", "Inventado", analizable)}),
    ("enriquecido", "cita literal de la transcripción",
     lambda t: {**t, "enriquecido": fila(t["enriquecido"], "cita", "El agente prometió un reembolso.", analizable)}),
    ("enriquecido", "omitidas sin notas ni cita",
     lambda t: {**t, "enriquecido": fila(t["enriquecido"], "saludo", 7, omitida)}),
    ("enriquecido", "una sola versión de análisis (la vigente)",
     lambda t: {**t, "enriquecido": fila(t["enriquecido"], "version_analisis", "version-vieja")}),
    ("negocio", "una evaluación por conversación analizable",
     lambda t: {**t, "evaluaciones": t["evaluaciones"].drop(
         t["evaluaciones"].index[t["evaluaciones"]["periodo"] == PERIODO][0])}),
    ("negocio", "pesos de la matriz suman 1", lambda t: {**t, "pesos": {**pipeline.PESOS, "cierre": 0.05}}),
    ("negocio", "puntaje ponderado de 1 a 10",
     lambda t: {**t, "evaluaciones": fila(t["evaluaciones"], "puntaje_ponderado", 10.5,
                                          lambda df: df["periodo"] == PERIODO)}),
    ("modelo", "hechos sin versión vigente de agente", lambda t: {**t, "hechos": fila(t["hechos"], "agente_sk", None)}),
    ("modelo", "grano conversación × parámetro sin duplicados",
     lambda t: {**t, "hechos": fila(t["hechos"], "parametro_sk", t["hechos"]["parametro_sk"].iloc[1])}),
    ("modelo", "hechos = evaluaciones × parámetros", lambda t: {**t, "hechos": t["hechos"].iloc[1:]}),
]


@pytest.mark.parametrize("capa", ["crudo", "enriquecido", "negocio", "modelo"])
def test_una_corrida_limpia_no_viola_ninguna_regla(capas: dict[str, pd.DataFrame], capa: str) -> None:
    assert evaluar(capa, capas) == dict.fromkeys(evaluar(capa, capas), 0)


@pytest.mark.parametrize("capa, regla, corromper", CORRUPCIONES, ids=[r for _, r, _ in CORRUPCIONES])
def test_cada_regla_detecta_su_defecto(capas: dict[str, pd.DataFrame], capa: str, regla: str, corromper) -> None:
    violaciones = evaluar(capa, corromper(dict(capas)))
    assert violaciones[regla] > 0, violaciones


def test_cada_regla_del_contrato_tiene_su_corrupcion(capas: dict[str, pd.DataFrame]) -> None:
    cubiertas = {(capa, regla) for capa, regla, _ in CORRUPCIONES}
    for capa in ["crudo", "enriquecido", "negocio", "modelo"]:
        assert {(capa, regla) for regla in evaluar(capa, capas)} <= cubiertas


def test_la_corrida_deja_un_reporte_aprobado_por_capa_y_periodo(corrida: Path) -> None:
    reportes = {p.stem: json.loads(p.read_text()) for p in (corrida / "informes" / "calidad").glob("*.json")}
    periodos = sorted(p.split("=")[1] for p in {r.parent.name for r in (corrida / "crudo").rglob("periodo=*/*.parquet")})
    esperados = {f"{capa}_{p}" for capa in ["crudo", "enriquecido", "negocio"] for p in periodos} | {"modelo_todos"}
    assert set(reportes) == esperados
    assert all(r["ok"] and all(regla["violaciones"] == 0 for regla in r["reglas"]) for r in reportes.values())


# --- La compuerta: auditar ANTES de publicar ------------------------------------------------
def test_una_violacion_en_la_extraccion_detiene_la_etapa_sin_publicar_la_capa(raiz: Path) -> None:
    # Un ID de voz que choca con el de un chat: dos conversaciones distintas con la misma clave.
    T = datetime(2026, 10, 5, 9, 0)
    crear_fuente([("C-1", "A01", T, 300, "COMPLETED", TEXTO_LARGO)], [("C-1", "A01", T, "CLIENTE", "Hola.")])

    with pytest.raises(SystemExit, match="conversacion_id único"):
        pipeline.etapa1_extraccion()

    assert not pipeline.ruta_capa("crudo", PERIODO).exists()
    reporte = json.loads((raiz / "informes" / "calidad" / f"crudo_{PERIODO}.json").read_text())
    assert not reporte["ok"]
    assert {r["regla"]: r["violaciones"] for r in reporte["reglas"]}["conversacion_id único y no nulo"] == 1


def test_una_salida_del_modelo_fuera_de_la_matriz_no_llega_a_la_capa_enriquecida(
    raiz: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generar_datos.main()
    pipeline.etapa1_extraccion()
    analizar = pipeline.analizar

    def proveedor_sin_validacion(texto: str) -> pipeline.Evaluacion:
        # Simula un proveedor cuya respuesta se aceptó sin validar contra el esquema.
        return pipeline.Evaluacion.model_construct(**{**analizar(texto).model_dump(), "solucion": 0})

    monkeypatch.setattr(pipeline, "analizar", proveedor_sin_validacion)

    with pytest.raises(SystemExit, match="notas de 1 a 10"):
        pipeline.etapa2_analisis()
    assert not pipeline.ruta_capa("enriquecido", pipeline.periodos_disponibles("crudo")[0]).exists()
