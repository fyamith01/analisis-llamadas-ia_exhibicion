"""Contratos del modelo estrella: grano, integridad, SCD tipo 2 y conciliación con pandas."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pytest
from apoyo import leer_capa

import consultas
import generar_datos
import pipeline


@pytest.fixture(scope="module")
def modelo(corrida: Path) -> dict[str, pd.DataFrame]:
    base = corrida / "negocio" / "modelo"
    tablas = {nombre: leer_capa(base / nombre)
              for nombre in ["hecho_evaluacion", "dim_agente", "dim_fecha", "dim_parametro", "dim_operacion"]}
    tablas["evaluaciones"] = leer_capa(corrida / "negocio" / "evaluaciones")
    return tablas


def test_el_grano_es_una_fila_por_conversacion_y_parametro(modelo: dict[str, pd.DataFrame]) -> None:
    hechos = modelo["hecho_evaluacion"]
    assert not hechos.duplicated(["conversacion_id", "parametro_sk"]).any()
    assert len(hechos) == len(modelo["evaluaciones"]) * len(pipeline.PESOS)


@pytest.mark.parametrize("dimension, clave", [
    ("dim_agente", "agente_sk"), ("dim_fecha", "fecha_sk"),
    ("dim_parametro", "parametro_sk"), ("dim_operacion", "operacion_sk"),
])
def test_cada_hecho_apunta_a_una_fila_existente_de_cada_dimension(
    modelo: dict[str, pd.DataFrame], dimension: str, clave: str
) -> None:
    assert modelo[dimension][clave].is_unique
    assert modelo["hecho_evaluacion"][clave].isin(modelo[dimension][clave]).all()


def test_el_puntaje_de_la_estrella_cuadra_con_el_de_pandas(modelo: dict[str, pd.DataFrame]) -> None:
    hechos = modelo["hecho_evaluacion"]
    estrella = (hechos["calificacion"] * hechos["peso"]).groupby(hechos["conversacion_id"]).sum()
    pandas = modelo["evaluaciones"].set_index("conversacion_id")["puntaje_ponderado"]
    pd.testing.assert_series_equal(estrella.sort_index(), pandas.sort_index(), check_names=False)


def test_las_versiones_de_cada_agente_son_contiguas_y_solo_una_es_vigente(modelo: dict[str, pd.DataFrame]) -> None:
    for agente_id, versiones in modelo["dim_agente"].sort_values("valido_desde").groupby("agente_id"):
        desde = pd.to_datetime(versiones["valido_desde"]).tolist()
        hasta = pd.to_datetime(versiones["valido_hasta"]).tolist()
        assert all(h + pd.Timedelta(days=1) == d for h, d in zip(hasta[:-1], desde[1:])), agente_id
        assert versiones["es_actual"].tolist() == [False] * (len(versiones) - 1) + [True], agente_id
        assert hasta[-1] == pd.Timestamp("9999-12-31"), agente_id


def test_cada_hecho_toma_la_version_del_agente_vigente_ese_dia(modelo: dict[str, pd.DataFrame]) -> None:
    unido = (modelo["hecho_evaluacion"]
             .merge(modelo["dim_agente"], on="agente_sk")
             .merge(modelo["dim_fecha"][["fecha_sk", "fecha"]], on="fecha_sk"))
    fecha = pd.to_datetime(unido["fecha"])
    assert fecha.between(pd.to_datetime(unido["valido_desde"]), pd.to_datetime(unido["valido_hasta"])).all()

    traslado = pd.Timestamp("2026-10-10")  # A07 pasa a Equipo Retención (ver generar_datos.ASIGNACIONES)
    a07 = unido[unido["agente_id"] == "A07"]
    assert set(a07.loc[fecha[a07.index] < traslado, "equipo"]) == {"Equipo Norte"}
    assert set(a07.loc[fecha[a07.index] >= traslado, "equipo"]) == {"Equipo Retención"}


def test_dim_fecha_es_un_calendario_completo_con_clave_aaaammdd(modelo: dict[str, pd.DataFrame]) -> None:
    fechas = pd.to_datetime(modelo["dim_fecha"].sort_values("fecha")["fecha"])
    esperado = pd.date_range(fechas.iloc[0].replace(day=1), fechas.iloc[-1] + pd.offsets.MonthEnd(0))
    assert fechas.tolist() == esperado.tolist()
    assert (modelo["dim_fecha"]["fecha_sk"] == pd.to_datetime(modelo["dim_fecha"]["fecha"]).dt.strftime("%Y%m%d").astype(int)).all()


def test_un_hecho_sin_version_vigente_detiene_el_pipeline(raiz: Path) -> None:
    generar_datos.main()
    pipeline.etapa1_extraccion()
    pipeline.etapa2_analisis()
    ruta = raiz / "crudo" / "asignaciones_agente" / "parte-0.parquet"
    asignaciones = pd.read_parquet(ruta)
    pipeline.escribir_atomico(asignaciones[asignaciones["agente_id"] != "A01"], ruta)

    with pytest.raises(SystemExit, match="sin versión vigente"):
        pipeline.etapa3_informe()


# --- Consultas sobre las capas y la estrella --------------------------------------------
def test_todas_las_consultas_devuelven_filas(corrida: Path) -> None:
    con = duckdb.connect()
    for titulo, sql in consultas.construir_consultas(corrida).items():
        assert len(con.execute(sql).df()) > 0, titulo


def test_sobrescribir_el_equipo_reatribuye_conversaciones_al_equipo_nuevo(corrida: Path) -> None:
    sql = next(s for t, s in consultas.construir_consultas(corrida).items() if "SCD1" in t)
    atribucion = duckdb.sql(sql).df().set_index("equipo")

    assert atribucion["con_scd2"].sum() == atribucion["sobrescribiendo"].sum()
    retencion = atribucion.loc["Equipo Retención"]
    assert retencion["sobrescribiendo"] > retencion["con_scd2"]
