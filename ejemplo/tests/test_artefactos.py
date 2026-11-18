"""Contrato de los artefactos multiagente de docs/artefactos/: herramientas restringidas y documento coherente."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ARTEFACTOS = Path(__file__).resolve().parents[2] / "docs" / "artefactos"
AGENTES = sorted((ARTEFACTOS / "agentes").glob("*.md"))
SOLO_LECTURA = {"Read", "Grep", "Glob"}
PERMITIDAS = SOLO_LECTURA | {"Bash"}  # Bash solo para ejecutar pruebas; nunca herramientas de edición


def frontmatter(ruta: Path) -> dict[str, str]:
    bloque = re.match(r"---\n(.*?)\n---\n", ruta.read_text(), re.DOTALL)
    assert bloque, f"{ruta.name} no tiene frontmatter"
    return dict(linea.split(": ", 1) for linea in bloque.group(1).splitlines())


def herramientas(ruta: Path) -> set[str]:
    return {h.strip() for h in frontmatter(ruta)["tools"].split(",")}


def test_hay_agentes_de_ejemplo() -> None:
    assert {a.stem for a in AGENTES} >= {"explorador", "verificador"}


@pytest.mark.parametrize("agente", AGENTES, ids=lambda a: a.stem)
def test_cada_agente_declara_nombre_descripcion_y_herramientas_permitidas(agente: Path) -> None:
    datos = frontmatter(agente)
    assert datos["name"] == agente.stem
    assert len(datos["description"]) > 40
    assert herramientas(agente) and herramientas(agente) <= PERMITIDAS, herramientas(agente)


def test_el_explorador_es_de_solo_lectura() -> None:
    assert herramientas(ARTEFACTOS / "agentes" / "explorador.md") <= SOLO_LECTURA


def test_el_documento_de_feature_tiene_sus_secciones_y_tareas_trazables() -> None:
    texto = (ARTEFACTOS / "documento-de-feature.md").read_text()
    for seccion in ["## Specs", "## Tasks", "## Criterios de aceptación", "## Log"]:
        assert seccion in texto, seccion

    specs = set(re.findall(r"^- \*\*(S\d+) ", texto, re.MULTILINE))
    assert specs and all(re.search(rf'\*\*{s} [^*]+\.\*\* Usuario: "', texto) for s in specs), "cada spec cita al usuario"

    filas = re.findall(r"^\| (T\d+) [^|]*\| ([^|]+) \| (inline|delegada) \| (\w+) \| ([^|]+) \|$", texto, re.MULTILINE)
    assert filas
    for tarea, referencias, _ruta, estado, evidencia in filas:
        citadas = set()
        for desde, hasta in re.findall(r"S(\d+)(?:-S(\d+))?", referencias):
            citadas |= {f"S{n}" for n in range(int(desde), int(hasta or desde) + 1)}
        assert citadas and citadas <= specs, (tarea, citadas - specs)
        assert estado != "hecho" or evidencia.strip(), f"{tarea} está hecha sin evidencia"


def test_los_enlaces_de_los_artefactos_existen() -> None:
    for archivo in [ARTEFACTOS / "README.md", ARTEFACTOS / "documento-de-feature.md"]:
        for destino in re.findall(r"\]\(([^)#]+?)\)", archivo.read_text()):
            if not destino.startswith("http"):
                assert (archivo.parent / destino).exists(), f"{archivo.name}: {destino}"
