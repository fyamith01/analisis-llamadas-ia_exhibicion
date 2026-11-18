"""Fixtures: cada prueba trabaja en un directorio temporal, nunca en `ejemplo/datos/`."""
from __future__ import annotations

from pathlib import Path

import pytest

import generar_datos
import pipeline


def apuntar_a(mp: pytest.MonkeyPatch, raiz: Path) -> None:
    """Redirige todas las rutas del ejemplo a `raiz`."""
    mp.setattr(pipeline, "RAIZ", raiz)
    mp.setattr(pipeline, "FUENTE", raiz / "fuente" / "fuente.duckdb")
    mp.setattr(generar_datos, "DESTINO", raiz / "fuente" / "fuente.duckdb")


@pytest.fixture
def raiz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Directorio de datos vacío y exclusivo de la prueba."""
    apuntar_a(monkeypatch, tmp_path)
    return tmp_path


@pytest.fixture(scope="session")
def corrida(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Una corrida completa sin interrupciones, compartida por las pruebas que solo leen."""
    raiz = tmp_path_factory.mktemp("corrida")
    with pytest.MonkeyPatch.context() as mp:
        apuntar_a(mp, raiz)
        generar_datos.main()
        pipeline.etapa1_extraccion()
        pipeline.etapa2_analisis()
        pipeline.etapa3_informe()
    return raiz
