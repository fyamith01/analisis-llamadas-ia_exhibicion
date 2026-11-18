"""Contratos de calidad entre capas: auditar antes de publicar (write-audit-publish).

Cada etapa calcula su salida en memoria, la pasa por el contrato de su capa y solo
la escribe si no hay violaciones. Así una capa defectuosa nunca llega a la siguiente
etapa ni a los tableros: el pipeline se detiene y el reporte dice qué regla falló y
en cuántas filas.

Cada regla devuelve el número de filas que la violan (0 = cumple). El reporte de
cada capa y periodo queda en `datos/informes/calidad/<capa>_<periodo>.json`, también
cuando todo pasa, para poder auditar después qué se verificó en cada corrida.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd

CANALES = ("voz", "whatsapp")


def _sin_nulos(df: pd.DataFrame, columnas: Iterable[str]) -> int:
    return int(df[list(columnas)].isna().any(axis=1).sum())


def _diferencia(a: Iterable[str], b: Iterable[str]) -> int:
    """Elementos que están en un lado y no en el otro (0 = mismos conjuntos)."""
    return len(set(a) ^ set(b))


def _fuera_de_rango(df: pd.DataFrame, columnas: Iterable[str], minimo: float, maximo: float) -> int:
    valores = df[list(columnas)]
    return int((valores.isna() | (valores < minimo) | (valores > maximo)).any(axis=1).sum())


# ----------------------------------------------------------------------------
# Contratos por capa
# ----------------------------------------------------------------------------
def contrato_crudo(df: pd.DataFrame, periodo: str, embudo: Mapping[str, int], min_caracteres: int) -> dict[str, int]:
    """Capa cruda: identidad, campos obligatorios, dominio y conciliación del embudo."""
    ids = df["conversacion_id"]
    return {
        "conversacion_id único y no nulo": int(ids.isna().sum() + ids.duplicated().sum()),
        "campos obligatorios sin nulos": _sin_nulos(df, ["agente_id", "fecha", "canal", "texto"]),
        "canal dentro del catálogo": int((~df["canal"].isin(CANALES)).sum()),
        "fecha dentro del periodo": int((pd.to_datetime(df["fecha"]).dt.strftime("%Y-%m") != periodo).sum()),
        # El embudo cuadra: lo que sale de la etapa es lo que el SQL seleccionó, sin filas perdidas ni duplicadas.
        "filas = voz seleccionada + chats": abs(len(df) - embudo["voz"] - embudo["chats"]),
        "voz seleccionada <= universo de voz": max(0, embudo["voz"] - embudo["universo_voz"]),
        "analizable coherente con el largo del texto": int(
            (df["analizable"] != (df["texto"].str.len() >= min_caracteres)).sum()
        ),
    }


def contrato_enriquecido(
    df: pd.DataFrame, ids_crudo: Iterable[str], parametros: Iterable[str], motivos: Iterable[str], version: str
) -> dict[str, int]:
    """Capa enriquecida: no se pierde ni se inventa ninguna conversación, y la salida del modelo respeta la matriz."""
    parametros = list(parametros)
    analizables = df[df["analizable"]]
    omitidas = df[~df["analizable"]]
    citas = analizables[["cita", "texto"]].dropna()
    return {
        "mismas conversaciones que la capa cruda": _diferencia(df["conversacion_id"], ids_crudo)
                                                  + int(df["conversacion_id"].duplicated().sum()),
        "notas de 1 a 10 en las analizables": _fuera_de_rango(analizables, parametros, 1, 10),
        "motivo dentro del catálogo": int((~analizables["motivo"].isin(list(motivos))).sum()),
        "cita literal de la transcripción": int(analizables["cita"].isna().sum())
                                            + sum(c not in t for c, t in zip(citas["cita"], citas["texto"])),
        "omitidas sin notas ni cita": int(omitidas[[*parametros, "cita"]].notna().any(axis=1).sum()),
        "una sola versión de análisis (la vigente)": int((df["version_analisis"] != version).sum()),
    }


def contrato_negocio(evaluaciones: pd.DataFrame, enriquecido: pd.DataFrame, pesos: Mapping[str, float]) -> dict[str, int]:
    """Capa de negocio: una evaluación por cada analizable y puntajes dentro de la escala."""
    analizables = enriquecido.loc[enriquecido["analizable"], "conversacion_id"]
    return {
        "una evaluación por conversación analizable": _diferencia(evaluaciones["conversacion_id"], analizables)
                                                     + int(evaluaciones["conversacion_id"].duplicated().sum()),
        "pesos de la matriz suman 1": int(abs(sum(pesos.values()) - 1) > 1e-9),
        "puntaje ponderado de 1 a 10": _fuera_de_rango(evaluaciones, ["puntaje_ponderado"], 1, 10),
    }


def contrato_modelo(hechos: pd.DataFrame, evaluaciones: pd.DataFrame, n_parametros: int) -> dict[str, int]:
    """Modelo estrella: integridad referencial, grano y conciliación con la tabla plana."""
    return {
        # Un hecho sin agente vigente es un error de datos: no se rellena con un agente "desconocido".
        "hechos sin versión vigente de agente": int(hechos["agente_sk"].isna().sum()),
        "grano conversación × parámetro sin duplicados": int(
            hechos.duplicated(["conversacion_id", "parametro_sk"]).sum()
        ),
        "hechos = evaluaciones × parámetros": abs(len(hechos) - len(evaluaciones) * n_parametros),
    }


# ----------------------------------------------------------------------------
# Auditoría
# ----------------------------------------------------------------------------
def auditar(capa: str, periodo: str, filas: int, violaciones: Mapping[str, int], destino: Path) -> None:
    """Escribe el reporte y detiene la etapa si alguna regla falla. Se llama ANTES de publicar la capa."""
    fallidas = {regla: n for regla, n in violaciones.items() if n}
    reporte = {
        "capa": capa,
        "periodo": periodo,
        "filas": filas,
        "ok": not fallidas,
        "reglas": [{"regla": regla, "violaciones": n} for regla, n in violaciones.items()],
    }
    ruta = destino / f"{capa}_{periodo}.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(reporte, ensure_ascii=False, indent=2))
    os.replace(tmp, ruta)

    if fallidas:
        detalle = "; ".join(f"{regla}: {n}" for regla, n in fallidas.items())
        raise SystemExit(f"[calidad] {capa} {periodo}: la capa NO se publica. Reglas violadas → {detalle}")
    print(f"[calidad] {capa} {periodo}: {len(violaciones)}/{len(violaciones)} reglas OK")
