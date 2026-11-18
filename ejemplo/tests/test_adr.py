"""Contrato de los ADR de docs/adr/: estructura fija, índice completo y enlaces vivos."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ADR = Path(__file__).resolve().parents[2] / "docs" / "adr"
REGISTROS = sorted(ADR.glob("[0-9][0-9][0-9][0-9]-*.md"))
SECCIONES = ["## Contexto", "## Decisión", "## Consecuencias", "## Alternativas descartadas"]


def test_los_adr_estan_numerados_sin_huecos() -> None:
    numeros = [int(r.name[:4]) for r in REGISTROS]
    assert numeros == list(range(1, len(numeros) + 1))


@pytest.mark.parametrize("registro", REGISTROS, ids=lambda r: r.stem)
def test_cada_adr_tiene_titulo_estado_y_secciones_en_orden(registro: Path) -> None:
    texto = registro.read_text()
    assert texto.startswith(f"# {registro.name[:4]}. ")
    assert re.search(r"^\*\*Estado:\*\* (propuesta|aceptada|reemplazada por \d{4}) · \*\*Alcance:\*\* ", texto, re.MULTILINE)
    posiciones = [texto.find(s) for s in SECCIONES]
    assert -1 not in posiciones and posiciones == sorted(posiciones), dict(zip(SECCIONES, posiciones))


def test_el_indice_lista_cada_adr_con_su_estado() -> None:
    indice = (ADR / "README.md").read_text()
    for registro in REGISTROS:
        fila = re.search(rf"^\| \[{registro.name[:4]}\]\({re.escape(registro.name)}\) \| [^|]+ \| (\w+) \|", indice, re.MULTILINE)
        assert fila, registro.name
        estado = re.search(r"^\*\*Estado:\*\* (\w+)", registro.read_text(), re.MULTILINE).group(1)
        assert fila.group(1).lower() == estado, registro.name


def test_los_enlaces_relativos_de_los_adr_existen() -> None:
    for archivo in [ADR / "README.md", *REGISTROS]:
        for destino in re.findall(r"\]\(([^)#]+?)(?:#[^)]*)?\)", archivo.read_text()):
            if not destino.startswith("http"):
                assert (archivo.parent / destino).exists(), f"{archivo.name}: {destino}"
