"""Auditoría multiagente: juez a ciegas, verificador de citas, kappa de Cohen y adaptadores de proveedor."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import ValidationError

import generar_datos
import multiagente
import pipeline

# El agente menciona un cobro, pero el cliente preguntó por su saldo.
ENGANOSA = ("Agente: Buenas tardes.\nCliente: ¿Cuál es el saldo de mi cuenta?\n"
            "Agente: Entiendo. Ya encontré el cobro duplicado y lo reverso hoy mismo.\nCliente: Muchas gracias.")


# --- Kappa de Cohen ---------------------------------------------------------------------
def test_kappa_vale_1_con_acuerdo_perfecto_y_0_con_acuerdo_de_azar() -> None:
    assert multiagente.kappa_cohen(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    # po = 0.5 y pe = 0.5: coinciden justo lo que se espera por azar.
    assert multiagente.kappa_cohen(["s", "n", "s", "n"], ["s", "s", "n", "n"]) == 0.0


def test_kappa_corrige_el_acuerdo_observado_por_el_esperado() -> None:
    # po = 0.75; pe = 0.5·0.25 + 0.5·0.75 = 0.5 → (0.75 − 0.5) / (1 − 0.5) = 0.5
    assert multiagente.kappa_cohen(["s", "s", "n", "n"], ["s", "n", "n", "n"]) == pytest.approx(0.5)
    # Con categorías desbalanceadas, 80 % de acuerdo puede ser peor que el azar:
    # po = 0.8; pe = 0.9·0.9 + 0.1·0.1 = 0.82 → (0.8 − 0.82) / 0.18 ≈ −0.111
    assert multiagente.kappa_cohen(["s"] * 9 + ["n"], ["n"] + ["s"] * 9) == pytest.approx(-1 / 9)


def test_kappa_con_una_sola_etiqueta_y_listas_invalidas() -> None:
    assert multiagente.kappa_cohen(["x", "x"], ["x", "x"]) == 1.0
    with pytest.raises(ValueError):
        multiagente.kappa_cohen(["a"], ["a", "b"])
    with pytest.raises(ValueError):
        multiagente.kappa_cohen([], [])


@pytest.mark.parametrize("kappa, nivel", [(-0.1, "pobre"), (0.15, "leve"), (0.5, "moderado"), (0.95, "casi perfecto")])
def test_interpretacion_de_landis_y_koch(kappa: float, nivel: str) -> None:
    assert multiagente.interpretar(kappa) == nivel


# --- Roles ------------------------------------------------------------------------------
class Grabadora(multiagente.Simulado):
    """Proveedor simulado que guarda lo que recibe y deja una marca en lo que devuelve."""

    def __init__(self) -> None:
        self.recibido: list[str] = []

    def generar(self, mensajes, esquema):
        self.recibido.append(json.dumps(mensajes, ensure_ascii=False))
        respuesta = super().generar(mensajes, esquema)
        if esquema is pipeline.Evaluacion:
            respuesta = respuesta.model_copy(update={"resumen": "MARCA-DEL-EVALUADOR"})
        return respuesta


def test_el_juez_no_ve_nada_de_lo_que_produjo_el_evaluador() -> None:
    evaluador, juez = Grabadora(), Grabadora()
    muestra = pd.DataFrame({"conversacion_id": ["X-1"], "canal": ["voz"], "texto": [ENGANOSA]})

    multiagente.auditar(muestra, evaluador, juez)

    assert len(juez.recibido) == 1
    assert "saldo de mi cuenta" in juez.recibido[0]
    assert "MARCA-DEL-EVALUADOR" not in juez.recibido[0]
    assert multiagente.SISTEMA_EVALUADOR not in juez.recibido[0]


def test_la_transcripcion_numerada_se_recupera_intacta() -> None:
    mensajes = multiagente.mensajes("sistema", ENGANOSA)
    assert "[1] Cliente: ¿Cuál es el saldo de mi cuenta?" in mensajes[-1]["content"]
    assert multiagente.transcripcion_de(mensajes) == ENGANOSA


def evaluacion(**cambios) -> pipeline.Evaluacion:
    base = {"fragmento_evidencia": 1, "resumen": "x", "saludo": 5, "escucha": 5, "solucion": 5, "cierre": 5,
            "motivo": "Consulta de saldo"}
    return pipeline.Evaluacion(**{**base, **cambios})


@pytest.mark.parametrize("cambios, fallas", [
    ({}, []),
    ({"fragmento_evidencia": 9}, ["el fragmento citado no existe"]),
    ({"fragmento_evidencia": -1}, ["el fragmento citado no existe"]),
    ({"motivo": "Cobro o facturación"}, ["la cita no respalda el motivo"]),
    ({"motivo": "Otro", "fragmento_evidencia": 0}, []),
])
def test_el_verificador_de_citas(cambios: dict, fallas: list[str]) -> None:
    assert multiagente.verificar_cita(ENGANOSA, evaluacion(**cambios)) == fallas


def test_un_desacuerdo_y_una_cita_que_no_respalda_van_a_revision_humana() -> None:
    muestra = pd.DataFrame({"conversacion_id": ["X-1"], "canal": ["voz"], "texto": [ENGANOSA]})

    fila = multiagente.auditar(muestra, multiagente.Simulado(), multiagente.Simulado()).iloc[0]

    assert fila["motivo_evaluador"] == "Cobro o facturación"  # el evaluador se dejó llevar por el agente
    assert fila["motivo_juez"] == "Consulta de saldo"
    assert fila["fallas_cita"] == "la cita no respalda el motivo"
    assert fila["requiere_revision"]



class EvaluadorFijo(multiagente.Simulado):
    """Devuelve siempre la misma evaluación, para aislar cada causa de revisión."""

    def __init__(self, **cambios) -> None:
        self.evaluacion = evaluacion(**cambios)

    def generar(self, mensajes, esquema):
        return self.evaluacion if esquema is pipeline.Evaluacion else super().generar(mensajes, esquema)


@pytest.mark.parametrize("cambios, resuelta, revision", [
    ({"solucion": 6}, True, False),                           # coincide con el juez en todo
    ({"solucion": 5}, False, True),                           # solo difiere en si quedó resuelta
    ({"solucion": 9, "fragmento_evidencia": 0}, True, True),  # solo falla la cita: cita el saludo
])
def test_cada_causa_por_separado_manda_o_no_a_revision(cambios: dict, resuelta: bool, revision: bool) -> None:
    muestra = pd.DataFrame({"conversacion_id": ["X-1"], "canal": ["voz"], "texto": [ENGANOSA]})

    fila = multiagente.auditar(muestra, EvaluadorFijo(**cambios), multiagente.Simulado()).iloc[0]

    assert fila["motivo_evaluador"] == fila["motivo_juez"] == "Consulta de saldo"
    assert fila["resuelta_juez"]
    assert fila["resuelta_evaluador"] == resuelta
    assert fila["requiere_revision"] == revision


def test_la_corrida_simulada_estratifica_mide_y_separa_la_revision(raiz: Path) -> None:
    generar_datos.main()
    pipeline.etapa1_extraccion()

    resumen = multiagente.ejecutar(multiagente.Simulado(), multiagente.Simulado(), por_canal=15)

    auditoria = pd.read_parquet(raiz / "multiagente" / "auditoria.parquet")
    revision = pd.read_parquet(raiz / "multiagente" / "revision_humana.parquet")
    assert auditoria["canal"].value_counts().to_dict() == {"voz": 15, "whatsapp": 15}
    assert auditoria["conversacion_id"].is_unique
    esperado = ((auditoria["motivo_evaluador"] != auditoria["motivo_juez"])
                | (auditoria["resuelta_evaluador"] != auditoria["resuelta_juez"])
                | (auditoria["fallas_cita"] != ""))
    assert auditoria["requiere_revision"].tolist() == esperado.tolist()
    assert sorted(revision["conversacion_id"]) == sorted(auditoria.loc[esperado, "conversacion_id"])
    assert resumen["motivo"]["kappa"] == round(
        multiagente.kappa_cohen(auditoria["motivo_evaluador"], auditoria["motivo_juez"]), 3)
    assert resumen == json.loads((raiz / "multiagente" / "resumen.json").read_text())


# --- Adaptadores (con clientes falsos: las pruebas no salen a la red) --------------------------
class ClienteOllamaFalso:
    def __init__(self, contenido: str) -> None:
        self.contenido, self.llamadas = contenido, []

    def chat(self, **kwargs):
        self.llamadas.append(kwargs)
        return SimpleNamespace(message=SimpleNamespace(content=self.contenido))


def test_ollama_pasa_el_esquema_en_format_y_valida_la_respuesta() -> None:
    etiqueta = multiagente.EtiquetaJuez(razonamiento="r", motivo="Consulta de saldo", resuelta=True)
    cliente = ClienteOllamaFalso(etiqueta.model_dump_json())
    ollama = multiagente.Ollama("modelo-x", cliente=cliente)

    assert multiagente.juzgar(ollama, ENGANOSA) == etiqueta
    llamada = cliente.llamadas[0]
    assert llamada["model"] == "modelo-x"
    assert llamada["format"] == multiagente.EtiquetaJuez.model_json_schema()
    assert llamada["options"]["num_ctx"] >= 8192
    assert llamada["messages"][0] == {"role": "system", "content": multiagente.SISTEMA_JUEZ}


def test_ollama_rechaza_una_respuesta_fuera_del_esquema() -> None:
    cliente = ClienteOllamaFalso('{"razonamiento": "r", "motivo": "Inventado", "resuelta": true}')
    with pytest.raises(ValidationError):
        multiagente.juzgar(multiagente.Ollama(cliente=cliente), ENGANOSA)


class ClienteClaudeFalso:
    def __init__(self, respuesta) -> None:
        self.respuesta, self.llamadas = respuesta, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self.parse))

    def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        return self.respuesta


def test_claude_usa_salida_estructurada_con_el_sistema_aparte_y_respaldo_ante_rechazos() -> None:
    esperado = evaluacion()
    cliente = ClienteClaudeFalso(SimpleNamespace(stop_reason="end_turn", parsed_output=esperado))

    assert multiagente.evaluar(multiagente.Claude(cliente=cliente), ENGANOSA) == esperado
    llamada = cliente.llamadas[0]
    assert llamada["model"] == "claude-opus-5-5"
    assert llamada["output_format"] is pipeline.Evaluacion
    assert llamada["system"] == multiagente.SISTEMA_EVALUADOR
    assert [m["role"] for m in llamada["messages"]] == ["user"]
    assert llamada["fallbacks"] == "default"


def test_claude_no_acepta_un_rechazo_como_respuesta() -> None:
    cliente = ClienteClaudeFalso(SimpleNamespace(stop_reason="refusal", parsed_output=None))
    with pytest.raises(RuntimeError, match="refusal"):
        multiagente.juzgar(multiagente.Claude(cliente=cliente), ENGANOSA)
