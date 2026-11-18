# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1", "pandas>=2.2", "pyarrow>=16"]
# ///
"""Consultas analíticas con DuckDB directamente sobre los Parquet particionados.

DuckDB lee solo las columnas y particiones que cada consulta necesita
(proyección de columnas, poda de particiones y predicate pushdown).
"""
from __future__ import annotations

from pathlib import Path

import duckdb

DATOS = Path(__file__).parent / "datos"
EVALUACIONES = f"read_parquet('{DATOS}/negocio/evaluaciones/**/*.parquet', hive_partitioning = true)"
ENRIQUECIDO = f"read_parquet('{DATOS}/enriquecido/conversaciones/**/*.parquet', hive_partitioning = true)"

CONSULTAS = {
    "Ranking de agentes por canal (último periodo)": f"""
        WITH ultimo AS (SELECT max(periodo) AS periodo FROM {EVALUACIONES}),
        puntajes AS (
            SELECT e.agente_id, e.canal,
                   round(avg(e.puntaje_ponderado), 2) AS promedio,
                   count(*)                           AS conversaciones
            FROM {EVALUACIONES} e JOIN ultimo u USING (periodo)
            GROUP BY ALL
        )
        SELECT *, rank() OVER (PARTITION BY canal ORDER BY promedio DESC) AS posicion
        FROM puntajes
        WHERE conversaciones >= 5
        ORDER BY canal, posicion
    """,
    "Variación del puntaje por agente entre periodos (LAG)": f"""
        WITH mensual AS (
            SELECT agente_id, periodo, round(avg(puntaje_ponderado), 2) AS promedio
            FROM {EVALUACIONES}
            GROUP BY ALL
        )
        SELECT agente_id, periodo, promedio,
               round(promedio - lag(promedio) OVER (PARTITION BY agente_id ORDER BY periodo), 2) AS variacion
        FROM mensual
        QUALIFY variacion IS NOT NULL
        ORDER BY variacion
    """,
    "Parámetro más débil por canal (UNPIVOT)": f"""
        WITH largo AS (
            UNPIVOT (SELECT canal, saludo, escucha, solucion, cierre FROM {EVALUACIONES})
            ON saludo, escucha, solucion, cierre INTO NAME parametro VALUE nota
        )
        SELECT canal, parametro, round(avg(nota), 2) AS promedio
        FROM largo
        GROUP BY canal, parametro
        QUALIFY row_number() OVER (PARTITION BY canal ORDER BY avg(nota)) = 1
    """,
    "Distribución de motivos con agregación condicional": f"""
        SELECT motivo,
               count(*) FILTER (WHERE canal = 'voz')      AS voz,
               count(*) FILTER (WHERE canal = 'whatsapp') AS whatsapp,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_total
        FROM {EVALUACIONES}
        GROUP BY motivo
        ORDER BY pct_total DESC
    """,
    "Citas literales de las conversaciones con peor solución": f"""
        SELECT conversacion_id, canal, solucion, cita
        FROM {ENRIQUECIDO}
        WHERE analizable AND solucion <= 3
        ORDER BY fecha
        LIMIT 5
    """,
    "Auditoría: filas omitidas (marcadas, nunca borradas)": f"""
        SELECT periodo, canal,
               count(*) FILTER (WHERE NOT analizable) AS omitidas,
               count(*)                               AS total
        FROM {ENRIQUECIDO}
        GROUP BY ALL
        ORDER BY ALL
    """,
}


def main() -> None:
    if not (DATOS / "negocio").exists():
        raise SystemExit("No hay capa de negocio: corre `uv run generar_datos.py` y `uv run pipeline.py`.")
    con = duckdb.connect()
    for titulo, sql in CONSULTAS.items():
        print(f"\n=== {titulo} ===")
        print(con.execute(sql).df().to_string(index=False))


if __name__ == "__main__":
    main()
