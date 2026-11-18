# Registro de decisiones de arquitectura (ADR)

Cada ADR documenta una decisión: el contexto que la forzó, qué se decidió, qué se ganó y qué se pagó, y qué alternativas se descartaron. Son cortos a propósito: el detalle vive en los capítulos y en el código.

| # | Decisión | Estado | Alcance |
|---|---|---|---|
| [0001](0001-tres-etapas-desacopladas.md) | Tres etapas desacopladas que se comunican por archivos | Aceptada | Sistema original y ejemplo |
| [0002](0002-parquet-entre-etapas.md) | Parquet entre etapas en lugar de una base de datos | Aceptada | Sistema original y ejemplo |
| [0003](0003-llm-local-con-api-como-referencia.md) | LLM local en producción y API como referencia de calidad | Aceptada | Sistema original y ejemplo |
| [0004](0004-contratos-de-calidad-antes-de-publicar.md) | Auditar cada capa antes de publicarla | Aceptada | Ejemplo |
| [0005](0005-delta-lake-como-evolucion.md) | Delta Lake como evolución, no como punto de partida | Propuesta | Ejemplo (variante) |
| [0006](0006-juez-a-ciegas-y-kappa.md) | Juez a ciegas y kappa de Cohen para medir el acuerdo entre modelos | Aceptada | Sistema original y ejemplo |

**Estados:** *propuesta* (demostrada, sin adoptar), *aceptada*, *reemplazada por NNNN*. Un ADR aceptado no se edita para cambiar la decisión: se escribe uno nuevo que lo reemplaza.

## Plantilla

```markdown
# NNNN. Título en forma de decisión

**Estado:** propuesta | aceptada | reemplazada por NNNN · **Alcance:** …

## Contexto
Qué problema o restricción obliga a decidir.

## Decisión
Qué se hace, en una o dos frases.

## Consecuencias
Lo que se gana y lo que se paga.

## Alternativas descartadas
Cada una con el motivo.
```
