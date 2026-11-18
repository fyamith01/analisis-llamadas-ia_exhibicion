# /// script
# requires-python = ">=3.11"
# dependencies = ["duckdb>=1.1"]
# ///
"""Genera una base de datos fuente SINTÉTICA que imita a los sistemas transaccionales.

- `llamadas`: una fila por llamada transcrita (metadatos + texto + estado).
- `mensajes_chat`: una fila por mensaje de WhatsApp (hay que reconstruir el diálogo).

Todo es inventado: agentes, clientes, frases y fechas.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

SEMILLA = 7
DESTINO = Path(__file__).parent / "datos" / "fuente" / "fuente.duckdb"
AGENTES = [f"A{n:02d}" for n in range(1, 9)]
PERIODOS = ["2026-09", "2026-10"]

SALUDOS = ["Buenos días, habla {a}, ¿con quién tengo el gusto?", "Hola, buenas tardes.", "Sí, diga."]
MOTIVOS = [
    "Quiero saber por qué me cobraron dos veces la cuota.",
    "Necesito actualizar mis datos de contacto.",
    "Estoy pensando en cancelar el servicio, no me han resuelto nada.",
    "¿Cuál es el saldo de mi cuenta?",
]
RESPUESTAS = [
    "Entiendo su molestia, permítame revisar. Ya encontré el cobro duplicado y lo reverso hoy mismo.",
    "Claro, con gusto. Le confirmo que sus datos quedaron actualizados.",
    "Eso no lo manejamos acá.",
    "Comprendo. Antes de cancelar, le cuento que tiene disponible un beneficio que le puede servir.",
]
CIERRES = ["¿Le puedo ayudar en algo más? Que tenga un excelente día.", "Listo.", ""]


def dialogo(rng: random.Random, agente: str) -> list[tuple[str, str]]:
    """Devuelve una conversación como lista de (remitente, texto)."""
    turnos = [("AGENTE", rng.choice(SALUDOS).format(a=agente)), ("CLIENTE", rng.choice(MOTIVOS))]
    for _ in range(rng.randint(1, 4)):
        turnos.append(("AGENTE", rng.choice(RESPUESTAS)))
        turnos.append(("CLIENTE", rng.choice(["Ok.", "No entiendo.", "Muchas gracias.", "Sigo esperando."])))
    cierre = rng.choice(CIERRES)
    if cierre:
        turnos.append(("AGENTE", cierre))
    return turnos


def main() -> None:
    rng = random.Random(SEMILLA)
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.unlink(missing_ok=True)

    llamadas, mensajes = [], []
    for periodo in PERIODOS:
        inicio = datetime.strptime(periodo + "-01", "%Y-%m-%d")
        for i in range(rng.randint(140, 180)):
            agente = rng.choice(AGENTES)
            fecha = inicio + timedelta(days=rng.randint(0, 27), minutes=rng.randint(0, 600))
            turnos = dialogo(rng, agente)
            if rng.random() < 0.55:  # canal voz: el texto ya viene transcrito
                texto = "\n".join(f"{'Agente' if r == 'AGENTE' else 'Cliente'}: {t}" for r, t in turnos)
                estado = "COMPLETED" if rng.random() > 0.05 else "FAILED"
                llamadas.append((f"V-{periodo}-{i}", agente, fecha, rng.randint(20, 900), estado, texto))
            else:  # canal chat: un registro por mensaje, desordenados a propósito
                conv = f"C-{periodo}-{i}"
                filas = [
                    (conv, agente, fecha + timedelta(seconds=30 * k), r, t) for k, (r, t) in enumerate(turnos)
                ]
                rng.shuffle(filas)
                mensajes.extend(filas)

    con = duckdb.connect(str(DESTINO))
    con.execute(
        "CREATE TABLE llamadas (id VARCHAR, agente_id VARCHAR, fecha TIMESTAMP,"
        " duracion_seg INTEGER, estado VARCHAR, texto VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mensajes_chat (conversacion_id VARCHAR, agente_id VARCHAR,"
        " enviado_en TIMESTAMP, remitente VARCHAR, mensaje VARCHAR)"
    )
    con.executemany("INSERT INTO llamadas VALUES (?, ?, ?, ?, ?, ?)", llamadas)
    con.executemany("INSERT INTO mensajes_chat VALUES (?, ?, ?, ?, ?)", mensajes)
    con.close()
    print(f"Fuente sintética creada en {DESTINO.relative_to(Path.cwd()) if DESTINO.is_relative_to(Path.cwd()) else DESTINO}")
    print(f"  llamadas: {len(llamadas)} · mensajes de chat: {len(mensajes)}")


if __name__ == "__main__":
    main()
