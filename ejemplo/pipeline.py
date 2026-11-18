# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1", "pandas>=2.2", "pyarrow>=16", "pydantic>=2.7"]
# ///
"""Ejemplo mínimo del patrón de 3 etapas comunicadas por Parquet.

    etapa 1  extracción   SQL sobre la fuente  → datos/crudo/       (bronce)
    etapa 2  análisis     "LLM" fila por fila  → datos/enriquecido/ (plata)
    etapa 3  informe      métricas en pandas   → datos/negocio/     (oro)
                          + modelo estrella    → datos/negocio/modelo/

El LLM está SIMULADO con reglas para que el ejemplo corra sin GPU. En el sistema
real, `analizar()` llama a un modelo con el JSON Schema de `Evaluacion` en `format=`.

Uso:
    uv run pipeline.py                     # las 3 etapas
    uv run pipeline.py --etapa 2 --interrumpir 40   # corta a las 40 filas
    uv run pipeline.py --etapa 2           # reanuda desde el checkpoint
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Literal

import duckdb
import pandas as pd
from pydantic import BaseModel, Field

RAIZ = Path(__file__).parent / "datos"
FUENTE = RAIZ / "fuente" / "fuente.duckdb"
OPERACION = "demo"
MIN_CARACTERES = 150
CADA_PCT = 5.0

# Matriz ilustrativa. Los pesos NO van al prompt: se aplican en la etapa 3.
PESOS = {"saludo": 0.20, "escucha": 0.30, "solucion": 0.35, "cierre": 0.15}
MOTIVOS = ["Cobro o facturación", "Actualización de datos", "Intención de cancelar", "Consulta de saldo", "Otro"]


# ----------------------------------------------------------------------------
# Esquema de salida: el razonamiento se declara ANTES de la etiqueta, porque
# el modelo genera los campos en el orden en que están declarados.
# ----------------------------------------------------------------------------
class Evaluacion(BaseModel):
    fragmento_evidencia: int = Field(description="Número del fragmento que respalda la evaluación")
    resumen: str = Field(description="Qué pidió el cliente y qué hizo el agente")
    saludo: int = Field(ge=1, le=10)
    escucha: int = Field(ge=1, le=10)
    solucion: int = Field(ge=1, le=10)
    cierre: int = Field(ge=1, le=10)
    motivo: Literal[tuple(MOTIVOS)]  # type: ignore[valid-type]


# ----------------------------------------------------------------------------
# Utilidades de almacenamiento
# ----------------------------------------------------------------------------
def ruta_capa(capa: str, periodo: str, tabla: str = "conversaciones") -> Path:
    """Particionado estilo Hive: la ruta codifica las dimensiones."""
    return RAIZ / capa / tabla / f"operacion={OPERACION}" / f"periodo={periodo}" / "parte-0.parquet"


def escribir_atomico(df: pd.DataFrame, destino: Path) -> None:
    """tmp + fsync + replace: un corte deja el archivo viejo o el nuevo, nunca uno a medias."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    df.to_parquet(tmp, index=False, compression="zstd")
    with open(tmp, "rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, destino)


def periodos_disponibles(capa: str, tabla: str = "conversaciones") -> list[str]:
    base = RAIZ / capa / tabla / f"operacion={OPERACION}"
    return sorted(p.name.split("=", 1)[1] for p in base.glob("periodo=*")) if base.exists() else []


# ----------------------------------------------------------------------------
# Etapa 1: extracción
# ----------------------------------------------------------------------------
SQL_VOZ = """
    SELECT id AS conversacion_id, agente_id, fecha, 'voz' AS canal, texto
    FROM llamadas
    WHERE estado = 'COMPLETED' AND duracion_seg > 60
      AND strftime(fecha, '%Y-%m') = ?
"""
# Los mensajes llegan desordenados: el diálogo se reconstruye ordenando por marca de tiempo.
SQL_CHAT = """
    SELECT conversacion_id, any_value(agente_id) AS agente_id, min(enviado_en) AS fecha,
           'whatsapp' AS canal,
           string_agg(CASE remitente WHEN 'AGENTE' THEN 'Agente: ' ELSE 'Cliente: ' END || mensaje,
                      chr(10) ORDER BY enviado_en) AS texto
    FROM mensajes_chat
    WHERE strftime(enviado_en, '%Y-%m') = ?
    GROUP BY conversacion_id
"""


def etapa1_extraccion() -> None:
    if not FUENTE.exists():
        raise SystemExit("Falta la fuente sintética: corre primero `uv run generar_datos.py`.")
    con = duckdb.connect(str(FUENTE), read_only=True)
    periodos = [r[0] for r in con.execute("SELECT DISTINCT strftime(fecha, '%Y-%m') FROM llamadas ORDER BY 1").fetchall()]
    for periodo in periodos:
        universo = con.execute("SELECT count(*) FROM llamadas WHERE strftime(fecha, '%Y-%m') = ?", [periodo]).fetchone()[0]
        voz = con.execute(SQL_VOZ, [periodo]).df()
        chat = con.execute(SQL_CHAT, [periodo]).df()
        df = pd.concat([voz, chat], ignore_index=True)
        # ORDER BY determinista: la huella del checkpoint depende del orden.
        df = df.sort_values(["fecha", "conversacion_id"], ignore_index=True)
        df["analizable"] = df["texto"].str.len() >= MIN_CARACTERES
        escribir_atomico(df, ruta_capa("crudo", periodo))
        print(f"[etapa 1] {periodo}: embudo voz {universo} → {len(voz)} (estado y duración) · "
              f"chats {len(chat)} · analizables {int(df['analizable'].sum())} de {len(df)}")

    # La bitácora de equipos no depende del periodo: alimenta la dimensión SCD2 de agentes.
    if "asignaciones_agente" not in {r[0] for r in con.execute("SHOW TABLES").fetchall()}:
        raise SystemExit("La fuente es de una versión anterior: vuelve a correr `uv run generar_datos.py`.")
    asignaciones = con.execute(
        "SELECT agente_id, equipo, desde FROM asignaciones_agente ORDER BY agente_id, desde"
    ).df()
    escribir_atomico(asignaciones, RAIZ / "crudo" / "asignaciones_agente" / "parte-0.parquet")
    print(f"[etapa 1] bitácora de equipos: {len(asignaciones)} asignaciones")
    con.close()


# ----------------------------------------------------------------------------
# Etapa 2: análisis
# ----------------------------------------------------------------------------
PROMPT = "Evalúa la conversación con la matriz de calidad. Devuelve SOLO el JSON del esquema."
MODELO = "simulado-v1"


def version_analisis() -> str:
    """Hash de prompt + esquema + modelo. Si algo cambia, el checkpoint viejo no se reutiliza."""
    esquema = json.dumps(Evaluacion.model_json_schema(), sort_keys=True)
    return hashlib.sha256(f"{PROMPT}|{esquema}|{MODELO}".encode()).hexdigest()[:12]


def huella_entrada(textos: pd.Series) -> str:
    """Sensible al orden y al número de filas."""
    h = hashlib.sha256()
    for t in textos:
        h.update(t.encode())
        h.update(b"\x00")
    return h.hexdigest()[:12]


def fragmentos(texto: str) -> list[str]:
    return [linea for linea in texto.split("\n") if linea.strip()]


def analizar(texto: str) -> Evaluacion:
    """LLM SIMULADO: reglas sencillas que devuelven el mismo esquema que el modelo real."""
    frag = fragmentos(texto)
    bajo = texto.lower()

    def tiene(*palabras: str) -> bool:
        return any(p in bajo for p in palabras)

    motivo = ("Cobro o facturación" if tiene("cobr") else "Actualización de datos" if tiene("datos")
              else "Intención de cancelar" if tiene("cancelar") else "Consulta de saldo" if tiene("saldo") else "Otro")
    evidencia = next((i for i, f in enumerate(frag) if f.startswith("Cliente")), 0)
    return Evaluacion(
        fragmento_evidencia=evidencia,
        resumen=f"El cliente consulta por {motivo.lower()}.",
        saludo=9 if tiene("buenos días", "buenas tardes") else 4,
        escucha=8 if tiene("entiendo", "comprendo") else 5,
        solucion=9 if tiene("reverso", "actualizados", "beneficio") else 3 if tiene("no lo manejamos") else 6,
        cierre=9 if tiene("algo más") else 3,
        motivo=motivo,
    )


def etapa2_analisis(interrumpir: int | None = None) -> None:
    periodos = periodos_disponibles("crudo")
    if not periodos:
        raise SystemExit("No hay capa cruda: corre la etapa 1. Nunca se generan datos de relleno.")
    for periodo in periodos:
        df = pd.read_parquet(ruta_capa("crudo", periodo))
        ruta_ck = RAIZ / "checkpoints" / f"{OPERACION}_{periodo}.json"
        clave = {"huella": huella_entrada(df["texto"]), "version": version_analisis()}

        hechos: dict[str, dict] = {}
        if ruta_ck.exists():
            guardado = json.loads(ruta_ck.read_text())
            if {k: guardado.get(k) for k in clave} == clave:
                hechos = guardado["resultados"]
                print(f"[etapa 2] {periodo}: se reanuda desde el checkpoint ({len(hechos)} filas hechas)")
            else:
                print(f"[etapa 2] {periodo}: checkpoint de otra entrada o versión; se descarta")

        def guardar() -> None:
            ruta_ck.parent.mkdir(parents=True, exist_ok=True)
            tmp = ruta_ck.with_suffix(".tmp")
            tmp.write_text(json.dumps({**clave, "resultados": hechos}))
            os.replace(tmp, ruta_ck)

        paso = max(1, round(len(df) * CADA_PCT / 100))
        nuevos = 0
        try:
            for i, fila in df.iterrows():
                if str(i) in hechos or not fila["analizable"]:
                    continue
                if interrumpir is not None and nuevos >= interrumpir:
                    raise KeyboardInterrupt
                hechos[str(i)] = analizar(fila["texto"]).model_dump()
                nuevos += 1
                if nuevos % paso == 0:
                    guardar()
        except KeyboardInterrupt:
            print(f"[etapa 2] {periodo}: interrumpida tras {nuevos} filas nuevas; el avance queda guardado")
            return
        finally:
            guardar()

        resultados = pd.DataFrame.from_dict(hechos, orient="index")
        resultados.index = resultados.index.astype(int)
        enriquecido = df.join(resultados)
        frag = enriquecido.apply(
            lambda f: fragmentos(f["texto"])[int(f["fragmento_evidencia"])] if pd.notna(f["fragmento_evidencia"]) else None,
            axis=1,
        )
        # La cita se toma de la transcripción: es literal por construcción.
        enriquecido["cita"] = frag.map(lambda s: re.sub(r"^\w+: ", "", s) if isinstance(s, str) else None)
        enriquecido["version_analisis"] = clave["version"]
        escribir_atomico(enriquecido, ruta_capa("enriquecido", periodo))
        print(f"[etapa 2] {periodo}: {resultados.shape[0]} analizadas · "
              f"{int((~df['analizable']).sum())} omitidas por texto corto (marcadas, no borradas)")


# ----------------------------------------------------------------------------
# Etapa 3: informe (todas las cifras en pandas)
# ----------------------------------------------------------------------------
def etapa3_informe() -> None:
    periodos = periodos_disponibles("enriquecido")
    if not periodos:
        raise SystemExit("No hay capa enriquecida: corre la etapa 2.")
    todas: list[pd.DataFrame] = []
    for periodo in periodos:
        df = pd.read_parquet(ruta_capa("enriquecido", periodo))
        df = df[df["analizable"]].copy()
        df["puntaje_ponderado"] = sum(df[p] * w for p, w in PESOS.items())

        por_agente = (df.groupby(["agente_id", "canal"])
                        .agg(conversaciones=("conversacion_id", "count"),
                             puntaje=("puntaje_ponderado", "mean"))
                        .round(2).reset_index())
        motivos = df["motivo"].value_counts(normalize=True).round(3)
        alertas = [f"'{m}' concentra el {p:.0%} de las filas: revisar si es colapso del modelo"
                   for m, p in motivos.items() if p > 0.40]

        # La capa enriquecida no se reescribe: el puntaje vive en la capa de negocio.
        columnas = ["conversacion_id", "agente_id", "fecha", "canal", "motivo", *PESOS,
                    "puntaje_ponderado", "version_analisis"]
        escribir_atomico(df[columnas], ruta_capa("negocio", periodo, "evaluaciones"))
        todas.append(df[columnas])
        escribir_atomico(por_agente, ruta_capa("negocio", periodo, "ranking_agentes"))
        indicadores = {
            "periodo": periodo,
            "conversaciones": int(len(df)),
            "puntaje_global": round(float(df["puntaje_ponderado"].mean()), 2),
            "por_canal": df.groupby("canal")["puntaje_ponderado"].mean().round(2).to_dict(),
            "motivos": motivos.to_dict(),
            "alertas": alertas,
        }
        salida = RAIZ / "informes" / f"indicadores_{periodo}.json"
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text(json.dumps(indicadores, ensure_ascii=False, indent=2))
        print(f"[etapa 3] {periodo}: puntaje global {indicadores['puntaje_global']} · "
              f"{len(alertas)} alerta(s) · {salida.relative_to(RAIZ.parent)}")

    construir_modelo_estrella(pd.concat(todas, ignore_index=True))


# ----------------------------------------------------------------------------
# Modelo dimensional (estrella) para consumo de BI
#
#   hecho_evaluacion: una fila por conversación × parámetro de la matriz
#   dim_agente:       SCD tipo 2 (una versión por cada cambio de equipo)
#   dim_fecha, dim_parametro, dim_operacion
# ----------------------------------------------------------------------------
def ruta_modelo(tabla: str) -> Path:
    return RAIZ / "negocio" / "modelo" / tabla / "parte-0.parquet"


SQL_DIM_AGENTE = """
    SELECT row_number() OVER (ORDER BY agente_id, desde)::INTEGER AS agente_sk,
           agente_id,
           equipo,
           desde AS valido_desde,
           -- La versión vence el día antes de que empiece la siguiente; la vigente no vence.
           coalesce((lead(desde) OVER w - INTERVAL 1 DAY)::DATE, DATE '9999-12-31') AS valido_hasta,
           lead(desde) OVER w IS NULL AS es_actual
    FROM asignaciones
    WINDOW w AS (PARTITION BY agente_id ORDER BY desde)
"""

# Calendario completo de los meses cubiertos, no solo los días con datos.
SQL_DIM_FECHA = """
    SELECT (year(d) * 10000 + month(d) * 100 + day(d))::INTEGER AS fecha_sk,
           d::DATE AS fecha, year(d) AS anio, month(d) AS mes,
           strftime(d, '%Y-%m') AS periodo,
           isodow(d) AS dia_semana, isodow(d) >= 6 AS es_fin_de_semana
    FROM generate_series(
        (SELECT date_trunc('month', min(fecha)) FROM evaluaciones),
        (SELECT last_day(max(fecha)) FROM evaluaciones),
        INTERVAL 1 DAY) AS t(d)
"""


def sql_hecho_evaluacion() -> str:
    parametros = ", ".join(PESOS)
    return f"""
        WITH largo AS (
            UNPIVOT (SELECT conversacion_id, agente_id, fecha, canal, version_analisis, {parametros}
                     FROM evaluaciones)
            ON {parametros} INTO NAME parametro VALUE calificacion
        )
        SELECT l.conversacion_id, a.agente_sk, o.operacion_sk, p.parametro_sk, f.fecha_sk,
               l.canal, l.calificacion::INTEGER AS calificacion, p.peso, l.version_analisis,
               f.periodo
        FROM largo l
        JOIN dim_parametro p USING (parametro)
        JOIN dim_fecha f ON f.fecha = l.fecha::DATE
        JOIN dim_operacion o ON o.operacion = '{OPERACION}'
        -- Búsqueda de clave sustituta "en el tiempo": la versión vigente el día de la conversación.
        LEFT JOIN dim_agente a
               ON a.agente_id = l.agente_id
              AND l.fecha::DATE BETWEEN a.valido_desde AND a.valido_hasta
    """


def construir_modelo_estrella(evaluaciones: pd.DataFrame) -> None:
    ruta_asignaciones = RAIZ / "crudo" / "asignaciones_agente" / "parte-0.parquet"
    if not ruta_asignaciones.exists():
        raise SystemExit("Falta la bitácora de equipos en la capa cruda: corre la etapa 1.")

    con = duckdb.connect()
    con.register("evaluaciones", evaluaciones)
    con.register("asignaciones", pd.read_parquet(ruta_asignaciones))
    dimensiones = {
        "dim_agente": con.execute(SQL_DIM_AGENTE).df(),
        "dim_fecha": con.execute(SQL_DIM_FECHA).df(),
        "dim_parametro": pd.DataFrame({"parametro_sk": range(1, len(PESOS) + 1),
                                       "parametro": list(PESOS), "peso": list(PESOS.values())}),
        "dim_operacion": pd.DataFrame({"operacion_sk": [1], "operacion": [OPERACION]}),
    }
    for nombre, dim in dimensiones.items():
        con.register(nombre, dim)
    hechos = con.execute(sql_hecho_evaluacion()).df()
    con.close()

    # Integridad referencial: un hecho sin agente vigente es un error de datos, no se rellena.
    huerfanos = int(hechos["agente_sk"].isna().sum())
    if huerfanos:
        raise SystemExit(f"[etapa 3] {huerfanos} hechos sin versión vigente de agente: revisa la bitácora de equipos.")
    hechos["agente_sk"] = hechos["agente_sk"].astype("int32")

    for nombre, dim in dimensiones.items():
        escribir_atomico(dim, ruta_modelo(nombre))
    for periodo, parte in hechos.groupby("periodo"):
        escribir_atomico(parte.drop(columns="periodo"), ruta_capa("negocio", periodo, "modelo/hecho_evaluacion"))

    agentes = dimensiones["dim_agente"]
    con_historial = int((agentes.groupby("agente_id").size() > 1).sum())
    print(f"[etapa 3] modelo estrella: {len(hechos)} hechos · {len(agentes)} versiones de agente "
          f"({con_historial} con cambio de equipo) · {len(dimensiones['dim_fecha'])} días en dim_fecha")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--etapa", type=int, choices=[1, 2, 3], help="ejecuta solo esa etapa")
    parser.add_argument("--interrumpir", type=int, help="corta la etapa 2 tras N filas nuevas (demo de reanudación)")
    args = parser.parse_args()

    if args.etapa in (None, 1):
        etapa1_extraccion()
    if args.etapa in (None, 2):
        etapa2_analisis(args.interrumpir)
    if args.etapa in (None, 3):
        etapa3_informe()


if __name__ == "__main__":
    main()
