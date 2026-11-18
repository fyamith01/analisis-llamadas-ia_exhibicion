# /// script
# requires-python = ">=3.11"
# dependencies = ["deltalake>=1.0", "duckdb>=1.1", "pandas>=2.2", "pyarrow>=16", "pydantic>=2.7"]
# ///
"""Variante con Delta Lake de la capa enriquecida (etapa 2).

Es una EVOLUCIÓN propuesta del diseño, no lo que usa el sistema real: allí la
capa enriquecida son archivos Parquet con un checkpoint JSON. Aquí la misma capa
es una tabla Delta (delta-rs, sin Spark ni JVM) y eso cambia tres cosas:

1. Reanudar sin checkpoint casero: cada lote analizado se confirma con un MERGE
   atómico. Si el proceso se corta, se pierde como mucho el lote en curso; al
   volver, solo se analiza lo que falta para la versión de prompt vigente.
2. Una fila por conversación: un prompt nuevo ACTUALIZA las filas (MERGE con
   condición sobre `version_analisis`) en lugar de escribir otra capa.
3. Time travel: los resultados del prompt anterior siguen disponibles en las
   versiones anteriores de la tabla, así que se pueden comparar los dos prompts.

Uso (requiere la capa cruda: `uv run pipeline.py --etapa 1`):
    uv run variante_delta.py                          # v1 → v2 → comparación
    uv run variante_delta.py --prompt v1 --interrumpir 40
    uv run variante_delta.py --prompt v1              # reanuda
    uv run variante_delta.py --comparar
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
from deltalake import CommitProperties, DeltaTable, write_deltalake

import calidad
import pipeline

LOTE = 25

PROMPT_V2 = (pipeline.PROMPT + " Si el cliente repite que no entiende o que sigue esperando, "
             "la solución no puede calificarse por encima de 4.")


def analizar_estricto(texto: str) -> pipeline.Evaluacion:
    """LLM SIMULADO con el prompt v2: más exigente con las quejas que siguen abiertas."""
    evaluacion = pipeline.analizar(texto)
    if "No entiendo" in texto or "Sigo esperando" in texto:
        evaluacion = evaluacion.model_copy(update={"solucion": min(evaluacion.solucion, 4)})
    return evaluacion


# Etiqueta → (prompt, analizador). La lambda resuelve `pipeline.analizar` en cada llamada.
PROMPTS: dict[str, tuple[str, Callable[[str], pipeline.Evaluacion]]] = {
    "v1": (pipeline.PROMPT, lambda texto: pipeline.analizar(texto)),
    "v2": (PROMPT_V2, lambda texto: analizar_estricto(texto)),
}


def ruta_tabla() -> Path:
    return pipeline.RAIZ / "delta" / "enriquecido"


def leer_crudo() -> pd.DataFrame:
    periodos = pipeline.periodos_disponibles("crudo")
    if not periodos:
        raise SystemExit("No hay capa cruda: corre `uv run pipeline.py --etapa 1`.")
    partes = [pd.read_parquet(pipeline.ruta_capa("crudo", p)).assign(periodo=p) for p in periodos]
    crudo = pd.concat(partes, ignore_index=True)
    return crudo[crudo["analizable"]].reset_index(drop=True)


def ya_analizadas(version: str) -> set[str]:
    """Conversaciones que ya tienen resultado de esta versión: lo que sobrevive a un corte."""
    if not DeltaTable.is_deltatable(str(ruta_tabla())):
        return set()
    tabla = DeltaTable(str(ruta_tabla())).to_pandas(columns=["conversacion_id", "version_analisis"])
    return set(tabla.loc[tabla["version_analisis"] == version, "conversacion_id"])


def evaluar_lote(lote: pd.DataFrame, analizar: Callable[[str], pipeline.Evaluacion], version: str,
                 etiqueta: str) -> pd.DataFrame:
    resultados = pd.DataFrame([analizar(texto).model_dump() for texto in lote["texto"]], index=lote.index)
    salida = lote.join(resultados)
    salida["cita"] = [pipeline.cita_literal(t, n) for t, n in zip(salida["texto"], salida["fragmento_evidencia"])]
    salida["version_analisis"] = version
    salida["prompt"] = etiqueta
    return salida.drop(columns=["texto", "analizable"])


def confirmar(lote: pd.DataFrame, etiqueta: str) -> None:
    """Un lote = una transacción. La metadata del commit deja el prompt en el historial de la tabla."""
    commit = CommitProperties(custom_metadata={"prompt": etiqueta})
    datos = pa.Table.from_pandas(lote, preserve_index=False)
    if not DeltaTable.is_deltatable(str(ruta_tabla())):
        write_deltalake(str(ruta_tabla()), datos, partition_by=["periodo"], commit_properties=commit)
        return
    (DeltaTable(str(ruta_tabla()))
        .merge(datos, predicate="t.conversacion_id = s.conversacion_id", source_alias="s", target_alias="t",
               commit_properties=commit)
        # Solo se reescribe lo que cambió de versión: repetir un lote ya confirmado no hace nada.
        .when_matched_update_all(predicate="t.version_analisis <> s.version_analisis")
        .when_not_matched_insert_all()
        .execute())


def analizar_delta(etiqueta: str = "v1", interrumpir: int | None = None) -> None:
    prompt, analizar = PROMPTS[etiqueta]
    version = pipeline.version_analisis(prompt)
    crudo = leer_crudo()
    hechas = ya_analizadas(version)
    pendientes = crudo[~crudo["conversacion_id"].isin(hechas)]
    if hechas:
        print(f"[delta] prompt {etiqueta}: {len(hechas)} conversaciones ya confirmadas; quedan {len(pendientes)}")

    confirmadas = 0
    for inicio in range(0, len(pendientes), LOTE):
        lote = pendientes.iloc[inicio:inicio + LOTE]
        if interrumpir is not None and confirmadas + len(lote) > interrumpir:
            perdidas = interrumpir - confirmadas
            print(f"[delta] prompt {etiqueta}: interrumpida; {confirmadas} filas confirmadas, "
                  f"{perdidas} del lote en curso se repetirán al reanudar")
            return
        salida = evaluar_lote(lote, analizar, version, etiqueta)
        # Auditar antes de confirmar: un lote que viola el contrato no entra a la tabla.
        violaciones = calidad.contrato_enriquecido(salida.assign(analizable=True, texto=lote["texto"]),
                                                   lote["conversacion_id"], pipeline.PESOS, pipeline.MOTIVOS, version)
        if any(violaciones.values()):
            fallidas = {r: n for r, n in violaciones.items() if n}
            raise SystemExit(f"[delta] el lote no se confirma. Reglas violadas → {fallidas}")
        confirmar(salida, etiqueta)
        confirmadas += len(lote)

    tabla = DeltaTable(str(ruta_tabla()))
    if confirmadas:
        # Cada lote deja un archivo pequeño por partición: OPTIMIZE los compacta (los viejos quedan
        # para el time travel hasta que un VACUUM los borre).
        compactacion = tabla.optimize.compact(commit_properties=CommitProperties(custom_metadata={"prompt": etiqueta}))
        if compactacion["numFilesRemoved"]:
            print(f"[delta] OPTIMIZE: {compactacion['numFilesRemoved']} archivos compactados en {compactacion['numFilesAdded']}")
    print(f"[delta] prompt {etiqueta}: {confirmadas} filas nuevas o actualizadas · "
          f"tabla en la versión {tabla.version()} con {tabla.to_pandas(columns=['conversacion_id']).shape[0]} conversaciones")


def ultima_version_de(tabla: DeltaTable, etiqueta: str) -> int:
    versiones = [h["version"] for h in tabla.history() if h.get("prompt") == etiqueta]
    if not versiones:
        raise SystemExit(f"No hay commits del prompt {etiqueta}: corre `uv run variante_delta.py --prompt {etiqueta}`.")
    return max(versiones)


def comparar(antes: str = "v1", despues: str = "v2") -> pd.DataFrame:
    """Time travel: la tabla tal como quedó con cada prompt, comparada con SQL."""
    tabla = DeltaTable(str(ruta_tabla()))
    historial = pd.DataFrame([
        {"version": h["version"], "operacion": h["operation"], "prompt": h.get("prompt"),
         "insertadas": h.get("operationMetrics", {}).get("num_target_rows_inserted",
                                                         h.get("operationMetrics", {}).get("num_added_rows", 0)),
         "actualizadas": h.get("operationMetrics", {}).get("num_target_rows_updated", 0)}
        for h in tabla.history()
    ])
    resumen = (historial.groupby(["prompt", "operacion"])
               .agg(commits=("version", "count"), desde=("version", "min"), hasta=("version", "max"),
                    insertadas=("insertadas", "sum"), actualizadas=("actualizadas", "sum"))
               .reset_index().sort_values("desde"))
    print("[delta] historial de la tabla (cada lote es un commit):")
    print(resumen.to_string(index=False))

    fotos = {}
    for etiqueta in (antes, despues):
        foto = DeltaTable(str(ruta_tabla()))
        foto.load_as_version(ultima_version_de(tabla, etiqueta))
        fotos[etiqueta] = foto.to_pyarrow_table()

    con = duckdb.connect()
    con.register("antes", fotos[antes])
    con.register("despues", fotos[despues])
    resultado = con.execute("""
        SELECT a.motivo,
               count(*)                                        AS conversaciones,
               round(avg(a.solucion), 2)                       AS solucion_antes,
               round(avg(d.solucion), 2)                       AS solucion_despues,
               count(*) FILTER (WHERE a.solucion <> d.solucion) AS cambiaron
        FROM antes a JOIN despues d USING (conversacion_id)
        GROUP BY a.motivo
        ORDER BY cambiaron DESC, a.motivo
    """).df()
    print(f"\n[delta] nota de solución por motivo: prompt {antes} frente a {despues}")
    print(resultado.to_string(index=False))
    return resultado


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prompt", choices=list(PROMPTS), help="analiza solo con esa versión de prompt")
    parser.add_argument("--interrumpir", type=int, help="corta tras N filas nuevas (demo de reanudación)")
    parser.add_argument("--comparar", action="store_true", help="solo compara v1 y v2 con time travel")
    args = parser.parse_args()

    if args.comparar:
        comparar()
    elif args.prompt:
        analizar_delta(args.prompt, args.interrumpir)
    else:
        analizar_delta("v1")
        analizar_delta("v2")
        comparar()


if __name__ == "__main__":
    main()
