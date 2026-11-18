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


def construir_consultas(datos: Path) -> dict[str, str]:
    """Devuelve {título: SQL} apuntando a las capas que hay bajo `datos`."""
    evaluaciones = f"read_parquet('{datos}/negocio/evaluaciones/**/*.parquet', hive_partitioning = true)"
    enriquecido = f"read_parquet('{datos}/enriquecido/conversaciones/**/*.parquet', hive_partitioning = true)"
    hechos = f"read_parquet('{datos}/negocio/modelo/hecho_evaluacion/**/*.parquet', hive_partitioning = true)"
    dim_agente = f"read_parquet('{datos}/negocio/modelo/dim_agente/*.parquet')"
    dim_fecha = f"read_parquet('{datos}/negocio/modelo/dim_fecha/*.parquet')"
    return {
        "Ranking de agentes por canal (último periodo)": f"""
            WITH ultimo AS (SELECT max(periodo) AS periodo FROM {evaluaciones}),
            puntajes AS (
                SELECT e.agente_id, e.canal,
                       round(avg(e.puntaje_ponderado), 2) AS promedio,
                       count(*)                           AS conversaciones
                FROM {evaluaciones} e JOIN ultimo u USING (periodo)
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
                FROM {evaluaciones}
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
                UNPIVOT (SELECT canal, saludo, escucha, solucion, cierre FROM {evaluaciones})
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
            FROM {evaluaciones}
            GROUP BY motivo
            ORDER BY pct_total DESC
        """,
        "Citas literales de las conversaciones con peor solución": f"""
            SELECT conversacion_id, canal, solucion, cita
            FROM {enriquecido}
            WHERE analizable AND solucion <= 3
            ORDER BY fecha
            LIMIT 5
        """,
        "Auditoría: filas omitidas (marcadas, nunca borradas)": f"""
            SELECT periodo, canal,
                   count(*) FILTER (WHERE NOT analizable) AS omitidas,
                   count(*)                               AS total
            FROM {enriquecido}
            GROUP BY ALL
            ORDER BY ALL
        """,
        # --- Modelo estrella: hechos + dimensiones -------------------------------------
        "Puntaje por equipo y periodo (estrella: hechos + dim_agente + dim_fecha)": f"""
            WITH por_conversacion AS (
                SELECT conversacion_id, agente_sk, fecha_sk, sum(calificacion * peso) AS puntaje
                FROM {hechos}
                GROUP BY ALL
            )
            SELECT f.periodo, a.equipo, count(*) AS conversaciones, round(avg(c.puntaje), 2) AS promedio
            FROM por_conversacion c
            JOIN {dim_agente} a USING (agente_sk)
            JOIN {dim_fecha} f USING (fecha_sk)
            GROUP BY ALL
            ORDER BY ALL
        """,
        "Historial de agentes que cambiaron de equipo (SCD tipo 2)": f"""
            SELECT a.agente_id, a.equipo, a.valido_desde, a.valido_hasta, a.es_actual,
                   count(DISTINCT h.conversacion_id) AS conversaciones
            FROM {dim_agente} a
            LEFT JOIN {hechos} h USING (agente_sk)
            WHERE a.agente_id IN (SELECT agente_id FROM {dim_agente} GROUP BY agente_id HAVING count(*) > 1)
            GROUP BY a.agente_id, a.equipo, a.valido_desde, a.valido_hasta, a.es_actual
            ORDER BY a.agente_id, a.valido_desde
        """,
        "Conversaciones por equipo: SCD2 frente a sobrescribir el equipo (SCD1)": f"""
            WITH atribucion AS (
                SELECT vigente.equipo AS equipo_vigente, actual.equipo AS equipo_actual
                FROM (SELECT DISTINCT conversacion_id, agente_sk FROM {hechos}) c
                JOIN {dim_agente} vigente USING (agente_sk)
                JOIN {dim_agente} actual ON actual.agente_id = vigente.agente_id AND actual.es_actual
            )
            SELECT equipo,
                   count(*) FILTER (WHERE origen = 'vigente') AS con_scd2,
                   count(*) FILTER (WHERE origen = 'actual')  AS sobrescribiendo
            FROM (SELECT equipo_vigente AS equipo, 'vigente' AS origen FROM atribucion
                  UNION ALL
                  SELECT equipo_actual, 'actual' FROM atribucion)
            GROUP BY equipo
            ORDER BY equipo
        """,
    }


def main() -> None:
    if not (DATOS / "negocio" / "modelo").exists():
        raise SystemExit("No hay capa de negocio: corre `uv run generar_datos.py` y `uv run pipeline.py`.")
    con = duckdb.connect()
    for titulo, sql in construir_consultas(DATOS).items():
        print(f"\n=== {titulo} ===")
        print(con.execute(sql).df().to_string(index=False))


if __name__ == "__main__":
    main()
