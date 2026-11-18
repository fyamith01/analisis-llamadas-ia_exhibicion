# 0004. Auditar cada capa antes de publicarla

**Estado:** aceptada · **Alcance:** ejemplo ([`calidad.py`](../../ejemplo/calidad.py))

## Contexto

Por [0001](0001-tres-etapas-desacopladas.md), cada capa es el contrato de entrada de la siguiente. La validación con Pydantic protege cada respuesta del modelo, pero no detecta defectos de la capa completa: una conversación perdida en un `JOIN`, una clave duplicada entre canales o un embudo que no cuadra. Un defecto así llega en silencio a los tableros.

## Decisión

Cada etapa calcula su salida en memoria, la pasa por el contrato de su capa y solo la escribe si no hay violaciones (*write-audit-publish*). Si alguna regla falla, la etapa se detiene. En todos los casos queda un reporte JSON por capa y periodo con las violaciones de cada regla.

## Consecuencias

- Una capa defectuosa nunca se publica: la etapa siguiente sigue leyendo la última capa buena.
- El reporte deja constancia de qué se verificó en cada corrida, también cuando todo pasa.
- Cada regla nueva necesita una prueba que la haga fallar; si no, puede quedar como una regla que nunca dispara.
- Detenerse es la opción conservadora: un periodo con un defecto menor también se queda sin informe hasta corregirlo.

## Alternativas descartadas

- **Escribir y validar después:** si el proceso se corta entre escribir y borrar, la capa defectuosa queda publicada.
- **Solo registrar advertencias:** nadie lee las advertencias de una corrida que terminó bien.
- **Un framework de calidad de datos (Great Expectations, Soda):** útil a escala, pero para 19 reglas sobre DataFrames en memoria agrega más configuración que la que ahorra.
