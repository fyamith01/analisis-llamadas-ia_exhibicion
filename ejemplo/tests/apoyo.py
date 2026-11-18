from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd


def leer_capa(directorio: Path) -> pd.DataFrame:
    """Lee una tabla particionada estilo Hive (o un directorio con un solo archivo)."""
    return duckdb.sql(
        f"SELECT * FROM read_parquet('{directorio}/**/*.parquet', hive_partitioning = true)"
    ).df()
