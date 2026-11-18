"""Variante Delta Lake: MERGE por lotes en lugar del checkpoint, una fila por conversación y time travel."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from apoyo import leer_capa
from deltalake import DeltaTable

import generar_datos
import pipeline
import variante_delta

NOTAS = ["fragmento_evidencia", *pipeline.PESOS, "motivo", "cita", "version_analisis"]


@pytest.fixture
def crudo(raiz: Path) -> pd.DataFrame:
    generar_datos.main()
    pipeline.etapa1_extraccion()
    return variante_delta.leer_crudo()


@pytest.fixture
def llamadas_al_modelo(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    llamadas: list[str] = []
    analizar = pipeline.analizar
    monkeypatch.setattr(pipeline, "analizar", lambda texto: llamadas.append(texto) or analizar(texto))
    return llamadas


def tabla(version: int | None = None) -> pd.DataFrame:
    delta = DeltaTable(str(variante_delta.ruta_tabla()))
    if version is not None:
        delta.load_as_version(version)
    return delta.to_pandas().set_index("conversacion_id").sort_index()


def test_v1_da_los_mismos_resultados_que_la_capa_enriquecida_en_parquet(crudo: pd.DataFrame, raiz: Path) -> None:
    pipeline.etapa2_analisis()
    variante_delta.analizar_delta("v1")

    parquet = leer_capa(raiz / "enriquecido" / "conversaciones")
    parquet = parquet[parquet["analizable"]].set_index("conversacion_id").sort_index()
    delta = tabla()
    assert delta.index.tolist() == parquet.index.tolist()
    pd.testing.assert_frame_equal(delta[NOTAS], parquet[NOTAS], check_dtype=False)


def test_reanudar_solo_analiza_lo_que_no_se_confirmo(crudo: pd.DataFrame, llamadas_al_modelo: list[str]) -> None:
    variante_delta.analizar_delta("v1", interrumpir=40)
    # Solo los lotes completos llegan a la tabla: el lote en curso se pierde y se repite.
    assert len(tabla()) == variante_delta.LOTE
    llamadas_al_modelo.clear()

    variante_delta.analizar_delta("v1")

    assert len(llamadas_al_modelo) == len(crudo) - variante_delta.LOTE
    assert tabla().index.tolist() == sorted(crudo["conversacion_id"])


def test_repetir_la_misma_version_no_llama_al_modelo_ni_crea_commits(
    crudo: pd.DataFrame, llamadas_al_modelo: list[str]
) -> None:
    variante_delta.analizar_delta("v1")
    version = DeltaTable(str(variante_delta.ruta_tabla())).version()
    llamadas_al_modelo.clear()

    variante_delta.analizar_delta("v1")

    assert llamadas_al_modelo == []
    assert DeltaTable(str(variante_delta.ruta_tabla())).version() == version


def test_un_prompt_nuevo_actualiza_en_sitio_y_el_anterior_sigue_en_el_historial(crudo: pd.DataFrame) -> None:
    variante_delta.analizar_delta("v1")
    v1 = tabla()
    variante_delta.analizar_delta("v2")
    v2 = tabla()

    # Una fila por conversación: el prompt nuevo no duplica, reemplaza.
    assert v2.index.tolist() == v1.index.tolist()
    assert set(v2["version_analisis"]) == {pipeline.version_analisis(variante_delta.PROMPT_V2)}
    assert set(v1["version_analisis"]) == {pipeline.version_analisis()}

    # Time travel: la última versión de la tabla confirmada con v1 conserva los resultados de v1.
    ultima_v1 = variante_delta.ultima_version_de(DeltaTable(str(variante_delta.ruta_tabla())), "v1")
    pd.testing.assert_frame_equal(tabla(ultima_v1), v1)

    # v2 solo baja la nota de solución de las quejas que siguen abiertas.
    abiertas = crudo.set_index("conversacion_id")["texto"].str.contains("No entiendo|Sigo esperando").sort_index()
    cambiaron = v1["solucion"] != v2["solucion"]
    assert cambiaron.any()
    assert not (cambiaron & ~abiertas).any()
    assert (v2.loc[abiertas, "solucion"] <= 4).all()


def test_la_comparacion_cuadra_con_las_dos_versiones(crudo: pd.DataFrame) -> None:
    variante_delta.analizar_delta("v1")
    v1 = tabla()
    variante_delta.analizar_delta("v2")
    v2 = tabla()

    resultado = variante_delta.comparar().set_index("motivo")

    assert resultado["conversaciones"].sum() == len(crudo)
    assert resultado["cambiaron"].sum() == int((v1["solucion"] != v2["solucion"]).sum())


def test_un_lote_que_viola_el_contrato_no_se_confirma(crudo: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> None:
    variante_delta.analizar_delta("v1")
    version = DeltaTable(str(variante_delta.ruta_tabla())).version()
    analizar = pipeline.analizar
    monkeypatch.setattr(pipeline, "analizar", lambda texto: pipeline.Evaluacion.model_construct(
        **{**analizar(texto).model_dump(), "motivo": "Inventado"}))

    with pytest.raises(SystemExit, match="motivo dentro del catálogo"):
        variante_delta.analizar_delta("v2")

    assert DeltaTable(str(variante_delta.ruta_tabla())).version() == version
    assert set(tabla()["prompt"]) == {"v1"}
