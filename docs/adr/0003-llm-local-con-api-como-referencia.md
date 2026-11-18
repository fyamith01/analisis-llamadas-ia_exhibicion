# 0003. LLM local en producción y API como referencia de calidad

**Estado:** aceptada · **Alcance:** sistema original y ejemplo

## Contexto

Cada conversación se califica con un modelo de lenguaje. Las transcripciones contienen datos personales de clientes finales. El volumen mensual es de miles de conversaciones y el servidor ya tiene una GPU de 16 GB que comparte con la transcripción nocturna.

## Decisión

La producción usa un modelo abierto en Ollama, en el servidor propio, con salida restringida por el JSON Schema del modelo Pydantic. La API de Claude es una ruta opcional, detrás de la misma interfaz, que se usa como referencia de calidad y para entregas puntuales.

## Consecuencias

- Los datos no salen del servidor en la operación mensual, y el costo es fijo.
- Hay que cuidar la operación del modelo local: presupuesto de VRAM, ventana de contexto explícita (`num_ctx`) y un esquema que el modelo pueda cumplir.
- La calidad del modelo local se mide contra la referencia; no se asume ([0006](0006-juez-a-ciegas-y-kappa.md)).
- Usar la API con datos reales exige un acuerdo de tratamiento de datos. En el ejemplo, los datos son sintéticos.

## Alternativas descartadas

- **Solo API:** mejor calidad en matrices largas, pero el costo es variable y los datos personales salen del servidor en cada corrida.
- **Solo modelo local, sin referencia:** más simple, pero sin forma de saber cuánto se pierde frente a un modelo más capaz.
- **El modelo más preciso del bake-off:** no cabía en la GPU con la concurrencia requerida junto a Whisper. Se eligió el siguiente y quedó documentado.
