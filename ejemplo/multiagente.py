# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1", "pandas>=2.2", "pyarrow>=16", "pydantic>=2.7"]
# ///
"""Auditoría multiagente de una muestra: evaluador + juez a ciegas + verificador de citas.

    evaluador   califica la conversación con la matriz (el mismo esquema de la etapa 2)
    juez        lee SOLO la transcripción, sin ver la evaluación, y decide el motivo
                y si el caso quedó resuelto
    verificador código determinista, no un modelo: comprueba que el fragmento citado
                exista y respalde el motivo elegido

Con los dos veredictos se mide el acuerdo con el kappa de Cohen (acuerdo corregido
por azar), y los desacuerdos y las citas que fallan van a una cola de revisión humana.

Los modelos están SIMULADOS por defecto (reglas distintas para cada rol), así que
corre sin GPU ni claves. Cada rol puede usar un proveedor real:

    uv run multiagente.py                                   # todo simulado
    uv run --with ollama multiagente.py --evaluador ollama  # evaluador local
    uv run --with anthropic multiagente.py --juez claude    # juez con la API de Claude
    uv run --with ollama --with anthropic multiagente.py --evaluador ollama --juez claude

Requiere la capa cruda: `uv run pipeline.py --etapa 1`.
"""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Sequence
from typing import Any, Literal, Protocol, TypeVar

import pandas as pd
from pydantic import BaseModel, Field

import pipeline

T = TypeVar("T", bound=BaseModel)
Mensajes = list[dict[str, str]]

UMBRAL_RESUELTA = 6  # una nota de solución >= 6 cuenta como "resuelta" para comparar con el juez
CLAVES_MOTIVO = {
    "Cobro o facturación": "cobr",
    "Actualización de datos": "datos",
    "Intención de cancelar": "cancel",
    "Consulta de saldo": "saldo",
}

# Prompts escritos para este ejemplo: no son los del sistema real.
SISTEMA_EVALUADOR = (
    "Eres evaluador de calidad de un contact center. Lee la transcripción numerada, elige el número del "
    "fragmento que mejor respalda el motivo del contacto y califica cada parámetro de 1 a 10. "
    "Responde solo con el JSON del esquema."
)
SISTEMA_JUEZ = (
    "Eres un auditor independiente. Lee la transcripción y decide por qué contactó el cliente y si el "
    "agente resolvió lo que pidió. Escribe tu razonamiento antes de decidir. "
    "Responde solo con el JSON del esquema."
)


class EtiquetaJuez(BaseModel):
    razonamiento: str = Field(description="Qué pidió el cliente y qué obtuvo")
    motivo: Literal[tuple(pipeline.MOTIVOS)]  # type: ignore[valid-type]
    resuelta: bool = Field(description="True si el agente resolvió lo que pidió el cliente")


# ----------------------------------------------------------------------------
# Mensajes: lo único que ve cada rol
# ----------------------------------------------------------------------------
def numerada(texto: str) -> str:
    return "\n".join(f"[{i}] {f}" for i, f in enumerate(pipeline.fragmentos(texto)))


def mensajes(sistema: str, texto: str) -> Mensajes:
    return [{"role": "system", "content": sistema},
            {"role": "user", "content": f"<transcripcion>\n{numerada(texto)}\n</transcripcion>"}]


def transcripcion_de(mensajes: Mensajes) -> str:
    """Recupera el texto original desde el mensaje del usuario (lo usa el proveedor simulado)."""
    cuerpo = re.search(r"<transcripcion>\n(.*)\n</transcripcion>", mensajes[-1]["content"], re.DOTALL)
    if cuerpo is None:
        raise ValueError("El mensaje no trae la transcripción entre etiquetas.")
    return re.sub(r"^\[\d+\] ", "", cuerpo.group(1), flags=re.MULTILINE)


# ----------------------------------------------------------------------------
# Proveedores: misma interfaz, intercambiables por rol
# ----------------------------------------------------------------------------
class Proveedor(Protocol):
    nombre: str

    def generar(self, mensajes: Mensajes, esquema: type[T]) -> T: ...


def juez_simulado(texto: str) -> EtiquetaJuez:
    """Reglas distintas a las del evaluador: el motivo sale de lo PRIMERO que dice el cliente."""
    cliente = [f for f in pipeline.fragmentos(texto) if f.startswith("Cliente")]
    apertura = cliente[0].lower() if cliente else ""
    motivo = next((m for m, clave in CLAVES_MOTIVO.items() if clave in apertura), "Otro")
    resolvio = any(p in texto.lower() for p in ("reverso", "actualizados", "beneficio"))
    sigue_abierta = bool(cliente) and any(p in cliente[-1] for p in ("No entiendo", "Sigo esperando"))
    return EtiquetaJuez(razonamiento=f"El cliente abre con: {cliente[0] if cliente else '(nada)'}",
                        motivo=motivo, resuelta=resolvio and not sigue_abierta)


class Simulado:
    """LLM SIMULADO: lee el mismo prompt que leería un modelo real y responde según el esquema pedido."""
    nombre = "simulado"

    def generar(self, mensajes: Mensajes, esquema: type[T]) -> T:
        texto = transcripcion_de(mensajes)
        if esquema is pipeline.Evaluacion:
            return pipeline.analizar(texto)  # type: ignore[return-value]
        if esquema is EtiquetaJuez:
            return juez_simulado(texto)  # type: ignore[return-value]
        raise ValueError(f"El proveedor simulado no conoce el esquema {esquema.__name__}.")


class Ollama:
    """Modelo local. `format=` recibe el JSON Schema: el modelo solo puede generar texto que lo cumpla."""

    def __init__(self, modelo: str = "qwen2.5:7b", host: str | None = None, cliente: Any = None) -> None:
        if cliente is None:
            import ollama  # dependencia opcional: uv run --with ollama …
            cliente = ollama.Client(host=host)
        self.cliente, self.modelo, self.nombre = cliente, modelo, f"ollama:{modelo}"

    def generar(self, mensajes: Mensajes, esquema: type[T]) -> T:
        respuesta = self.cliente.chat(
            model=self.modelo, messages=mensajes, format=esquema.model_json_schema(),
            # Sin num_ctx explícito Ollama trunca el prompt por el principio y se pierden las instrucciones.
            options={"temperature": 0, "num_ctx": 8192},
        )
        return esquema.model_validate_json(respuesta.message.content)


class Claude:
    """API de Claude con salida estructurada: el SDK valida la respuesta contra el modelo Pydantic."""

    def __init__(self, modelo: str = "claude-opus-5-5", cliente: Any = None) -> None:
        if cliente is None:
            import anthropic  # dependencia opcional: uv run --with anthropic …
            cliente = anthropic.Anthropic()  # credenciales: ANTHROPIC_API_KEY o `ant auth login`
        self.cliente, self.modelo, self.nombre = cliente, modelo, f"claude:{modelo}"

    def generar(self, mensajes: Mensajes, esquema: type[T]) -> T:
        sistema = "\n".join(m["content"] for m in mensajes if m["role"] == "system")
        respuesta = self.cliente.beta.messages.parse(
            model=self.modelo,
            max_tokens=16000,
            system=sistema,
            messages=[m for m in mensajes if m["role"] != "system"],
            output_format=esquema,
            output_config={"effort": "medium"},
            # Si un clasificador de seguridad rechaza la petición, la API la reintenta con otro modelo.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if respuesta.stop_reason == "refusal" or respuesta.parsed_output is None:
            raise RuntimeError(f"{self.nombre} no devolvió una respuesta válida (stop_reason={respuesta.stop_reason}).")
        return respuesta.parsed_output


def proveedor(nombre: str, args: argparse.Namespace) -> Proveedor:
    if nombre == "ollama":
        return Ollama(args.modelo_ollama)
    if nombre == "claude":
        return Claude(args.modelo_claude)
    return Simulado()


# ----------------------------------------------------------------------------
# Roles
# ----------------------------------------------------------------------------
def evaluar(evaluador: Proveedor, texto: str) -> pipeline.Evaluacion:
    return evaluador.generar(mensajes(SISTEMA_EVALUADOR, texto), pipeline.Evaluacion)


def juzgar(juez: Proveedor, texto: str) -> EtiquetaJuez:
    """A ciegas por construcción: la firma no recibe la evaluación, así que no puede filtrarse al prompt."""
    return juez.generar(mensajes(SISTEMA_JUEZ, texto), EtiquetaJuez)


def verificar_cita(texto: str, evaluacion: pipeline.Evaluacion) -> list[str]:
    """Verificador determinista: el fragmento citado existe y menciona el motivo elegido."""
    fragmentos = pipeline.fragmentos(texto)
    if not 0 <= evaluacion.fragmento_evidencia < len(fragmentos):
        return ["el fragmento citado no existe"]
    clave = CLAVES_MOTIVO.get(evaluacion.motivo)
    if clave and clave not in fragmentos[evaluacion.fragmento_evidencia].lower():
        return ["la cita no respalda el motivo"]
    return []


# ----------------------------------------------------------------------------
# Métrica de acuerdo
# ----------------------------------------------------------------------------
def kappa_cohen(a: Sequence[object], b: Sequence[object]) -> float:
    """Acuerdo observado corregido por el acuerdo esperado por azar: (po - pe) / (1 - pe)."""
    a, b = list(a), list(b)
    if len(a) != len(b) or not a:
        raise ValueError("Se necesitan dos listas no vacías del mismo largo.")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in set(a) | set(b))
    if pe == 1:  # los dos usan siempre la misma etiqueta: no hay variación que medir
        return 1.0 if po == 1 else 0.0
    return (po - pe) / (1 - pe)


def interpretar(kappa: float) -> str:
    """Bandas de Landis y Koch (1977)."""
    for limite, nombre in [(0.0, "pobre"), (0.20, "leve"), (0.40, "aceptable"), (0.60, "moderado"),
                           (0.80, "sustancial")]:
        if kappa <= limite:
            return nombre
    return "casi perfecto"


# ----------------------------------------------------------------------------
# Orquestación
# ----------------------------------------------------------------------------
def muestra_estratificada(crudo: pd.DataFrame, por_canal: int, semilla: int = 7) -> pd.DataFrame:
    """Muestra piloto: el mismo número de conversaciones por canal, para que ninguno domine el acuerdo."""
    analizables = crudo[crudo["analizable"]]
    partes = [g.sample(min(por_canal, len(g)), random_state=semilla) for _, g in analizables.groupby("canal")]
    return pd.concat(partes).sort_values("conversacion_id", ignore_index=True)


def auditar(muestra: pd.DataFrame, evaluador: Proveedor, juez: Proveedor) -> pd.DataFrame:
    filas = []
    for fila in muestra.itertuples(index=False):
        evaluacion = evaluar(evaluador, fila.texto)
        etiqueta = juzgar(juez, fila.texto)
        fallas = verificar_cita(fila.texto, evaluacion)
        filas.append({
            "conversacion_id": fila.conversacion_id,
            "canal": fila.canal,
            "motivo_evaluador": evaluacion.motivo,
            "motivo_juez": etiqueta.motivo,
            "resuelta_evaluador": evaluacion.solucion >= UMBRAL_RESUELTA,
            "resuelta_juez": etiqueta.resuelta,
            "fallas_cita": "; ".join(fallas),
            "razonamiento_juez": etiqueta.razonamiento,
        })
    auditoria = pd.DataFrame(filas)
    auditoria["requiere_revision"] = (
        (auditoria["motivo_evaluador"] != auditoria["motivo_juez"])
        | (auditoria["resuelta_evaluador"] != auditoria["resuelta_juez"])
        | (auditoria["fallas_cita"] != "")
    )
    return auditoria


def resumir(auditoria: pd.DataFrame, evaluador: Proveedor, juez: Proveedor) -> dict[str, Any]:
    def acuerdo(campo: str) -> dict[str, Any]:
        a, b = auditoria[f"{campo}_evaluador"], auditoria[f"{campo}_juez"]
        kappa = round(kappa_cohen(a, b), 3)
        return {"kappa": kappa, "nivel": interpretar(kappa), "acuerdo_observado": round(float((a == b).mean()), 3)}

    return {
        "evaluador": evaluador.nombre,
        "juez": juez.nombre,
        "conversaciones": len(auditoria),
        "por_canal": auditoria["canal"].value_counts().sort_index().to_dict(),
        "motivo": acuerdo("motivo"),
        "resuelta": acuerdo("resuelta"),
        "citas_verificadas": round(float((auditoria["fallas_cita"] == "").mean()), 3),
        "a_revision_humana": int(auditoria["requiere_revision"].sum()),
    }


def ejecutar(evaluador: Proveedor, juez: Proveedor, por_canal: int = 20) -> dict[str, Any]:
    periodos = pipeline.periodos_disponibles("crudo")
    if not periodos:
        raise SystemExit("No hay capa cruda: corre `uv run pipeline.py --etapa 1`.")
    crudo = pd.concat([pd.read_parquet(pipeline.ruta_capa("crudo", p)) for p in periodos], ignore_index=True)
    auditoria = auditar(muestra_estratificada(crudo, por_canal), evaluador, juez)
    resumen = resumir(auditoria, evaluador, juez)

    destino = pipeline.RAIZ / "multiagente"
    pipeline.escribir_atomico(auditoria, destino / "auditoria.parquet")
    pipeline.escribir_atomico(auditoria[auditoria["requiere_revision"]], destino / "revision_humana.parquet")
    (destino / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2))

    print(f"[multiagente] evaluador {resumen['evaluador']} · juez {resumen['juez']} · "
          f"{resumen['conversaciones']} conversaciones {resumen['por_canal']}")
    for campo in ("motivo", "resuelta"):
        r = resumen[campo]
        print(f"[multiagente] {campo}: kappa {r['kappa']} ({r['nivel']}) · acuerdo observado {r['acuerdo_observado']:.0%}")
    print(f"[multiagente] citas verificadas {resumen['citas_verificadas']:.0%} · "
          f"{resumen['a_revision_humana']} a revisión humana → {destino.relative_to(pipeline.RAIZ.parent)}/")
    return resumen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    opciones = ["simulado", "ollama", "claude"]
    parser.add_argument("--evaluador", choices=opciones, default="simulado")
    parser.add_argument("--juez", choices=opciones, default="simulado")
    parser.add_argument("--por-canal", type=int, default=20, help="conversaciones por canal en la muestra")
    parser.add_argument("--modelo-ollama", default="qwen2.5:7b")
    parser.add_argument("--modelo-claude", default="claude-opus-5-5")
    args = parser.parse_args()
    ejecutar(proveedor(args.evaluador, args), proveedor(args.juez, args), args.por_canal)


if __name__ == "__main__":
    main()
