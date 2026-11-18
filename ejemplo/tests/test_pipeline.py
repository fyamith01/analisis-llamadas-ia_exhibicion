"""Comportamiento de las 3 etapas: almacenamiento, extracción, análisis y reanudación."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from apoyo import leer_capa
from pydantic import ValidationError

import generar_datos
import pipeline

T = datetime(2026, 10, 5, 9, 0)
TEXTO_LARGO = "Agente: Buenos días.\n" + "Cliente: Quiero saber por qué me cobraron dos veces la cuota.\n" * 3


def crear_fuente(llamadas: list[tuple], mensajes: list[tuple] = ()) -> None:
    """Fuente mínima a la medida de la prueba, con el mismo esquema que `generar_datos`."""
    pipeline.FUENTE.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(pipeline.FUENTE))
    con.execute("CREATE TABLE llamadas (id VARCHAR, agente_id VARCHAR, fecha TIMESTAMP,"
                " duracion_seg INTEGER, estado VARCHAR, texto VARCHAR)")
    con.execute("CREATE TABLE mensajes_chat (conversacion_id VARCHAR, agente_id VARCHAR,"
                " enviado_en TIMESTAMP, remitente VARCHAR, mensaje VARCHAR)")
    con.execute("CREATE TABLE asignaciones_agente (agente_id VARCHAR, equipo VARCHAR, desde DATE)")
    con.executemany("INSERT INTO llamadas VALUES (?, ?, ?, ?, ?, ?)", llamadas)
    if mensajes:
        con.executemany("INSERT INTO mensajes_chat VALUES (?, ?, ?, ?, ?)", mensajes)
    con.execute("INSERT INTO asignaciones_agente VALUES ('A01', 'Equipo Norte', ?)", [date(2026, 1, 1)])
    con.close()


# --- Almacenamiento -------------------------------------------------------------------
def test_escritura_atomica_reemplaza_sin_dejar_temporales(tmp_path: Path) -> None:
    destino = tmp_path / "tabla" / "parte-0.parquet"
    pipeline.escribir_atomico(pd.DataFrame({"v": [1]}), destino)
    pipeline.escribir_atomico(pd.DataFrame({"v": [2, 3]}), destino)

    assert pd.read_parquet(destino)["v"].tolist() == [2, 3]
    assert [p.name for p in destino.parent.iterdir()] == ["parte-0.parquet"]


def test_la_particion_va_en_la_ruta_estilo_hive() -> None:
    ruta = pipeline.ruta_capa("crudo", "2026-10")
    assert ruta.parts[-3:] == ("operacion=demo", "periodo=2026-10", "parte-0.parquet")


# --- Etapa 1: extracción ----------------------------------------------------------------
def test_el_chat_se_reconstruye_en_orden_de_envio(raiz: Path) -> None:
    desordenados = [
        ("C-1", "A01", T + timedelta(seconds=60), "CLIENTE", "tercero"),
        ("C-1", "A01", T, "AGENTE", "primero"),
        ("C-1", "A01", T + timedelta(seconds=30), "CLIENTE", "segundo"),
    ]
    crear_fuente([("V-1", "A01", T, 300, "COMPLETED", TEXTO_LARGO)], desordenados)

    pipeline.etapa1_extraccion()

    chat = pd.read_parquet(pipeline.ruta_capa("crudo", "2026-10")).set_index("conversacion_id").loc["C-1"]
    assert chat["texto"] == "Agente: primero\nCliente: segundo\nCliente: tercero"
    assert chat["canal"] == "whatsapp"


def test_el_embudo_excluye_fallidas_y_cortas_y_marca_los_textos_breves(raiz: Path) -> None:
    crear_fuente([
        ("V-ok", "A01", T, 300, "COMPLETED", TEXTO_LARGO),
        ("V-fallida", "A01", T, 300, "FAILED", TEXTO_LARGO),
        ("V-corta", "A01", T, 30, "COMPLETED", TEXTO_LARGO),
        ("V-breve", "A01", T, 300, "COMPLETED", "Agente: Hola."),
    ])

    pipeline.etapa1_extraccion()

    crudo = pd.read_parquet(pipeline.ruta_capa("crudo", "2026-10")).set_index("conversacion_id")
    assert crudo["analizable"].to_dict() == {"V-ok": True, "V-breve": False}


# --- Etapa 2: análisis ------------------------------------------------------------------
VALIDA = {"fragmento_evidencia": 0, "resumen": "x", "saludo": 5, "escucha": 5, "solucion": 5,
          "cierre": 5, "motivo": "Otro"}


@pytest.mark.parametrize("cambio", [{"saludo": 0}, {"cierre": 11}, {"motivo": "Inventado"}])
def test_el_esquema_rechaza_valores_fuera_de_la_matriz(cambio: dict) -> None:
    pipeline.Evaluacion(**VALIDA)
    with pytest.raises(ValidationError):
        pipeline.Evaluacion(**{**VALIDA, **cambio})


def test_el_razonamiento_se_declara_antes_que_las_notas() -> None:
    campos = list(pipeline.Evaluacion.model_fields)
    assert campos.index("resumen") < min(campos.index(p) for p in pipeline.PESOS)


def test_la_huella_depende_del_orden_y_de_los_limites_entre_textos() -> None:
    huella = pipeline.huella_entrada
    assert huella(pd.Series(["a", "b"])) != huella(pd.Series(["b", "a"]))
    assert huella(pd.Series(["a", "b"])) != huella(pd.Series(["ab"]))


def test_la_cita_es_literal_y_las_omitidas_se_conservan(corrida: Path) -> None:
    crudo = leer_capa(corrida / "crudo" / "conversaciones")
    enriquecido = leer_capa(corrida / "enriquecido" / "conversaciones")
    analizables = enriquecido[enriquecido["analizable"]]

    assert len(enriquecido) == len(crudo)
    assert (~enriquecido["analizable"]).sum() > 0
    assert enriquecido.loc[~enriquecido["analizable"], "cita"].isna().all()
    assert all(cita in texto for cita, texto in zip(analizables["cita"], analizables["texto"]))


def test_reanudar_tras_una_interrupcion_da_lo_mismo_que_una_corrida_completa(
    raiz: Path, corrida: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generar_datos.main()
    pipeline.etapa1_extraccion()

    pipeline.etapa2_analisis(interrumpir=40)
    primer_periodo = pipeline.periodos_disponibles("crudo")[0]
    checkpoint = json.loads((raiz / "checkpoints" / f"demo_{primer_periodo}.json").read_text())
    assert len(checkpoint["resultados"]) == 40

    llamadas_al_modelo = []
    analizar = pipeline.analizar
    monkeypatch.setattr(pipeline, "analizar", lambda texto: llamadas_al_modelo.append(1) or analizar(texto))
    pipeline.etapa2_analisis()

    # Las 40 filas ya hechas no vuelven al modelo: en producción cada una cuesta segundos de GPU.
    analizables = int(leer_capa(raiz / "crudo" / "conversaciones")["analizable"].sum())
    assert len(llamadas_al_modelo) == analizables - 40
    for periodo in pipeline.periodos_disponibles("crudo"):
        ruta = pipeline.ruta_capa("enriquecido", periodo)
        pd.testing.assert_frame_equal(pd.read_parquet(ruta), pd.read_parquet(corrida / ruta.relative_to(raiz)))


def test_cambiar_el_modelo_descarta_el_checkpoint(
    raiz: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    generar_datos.main()
    pipeline.etapa1_extraccion()
    pipeline.etapa2_analisis(interrumpir=10)

    monkeypatch.setattr(pipeline, "MODELO", "otro-modelo")
    pipeline.etapa2_analisis()

    assert "se descarta" in capsys.readouterr().out
    enriquecido = leer_capa(raiz / "enriquecido" / "conversaciones")
    assert (enriquecido["version_analisis"] == pipeline.version_analisis()).all()
