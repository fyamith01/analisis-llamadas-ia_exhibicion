# 0001. Tres etapas desacopladas que se comunican por archivos

**Estado:** aceptada · **Alcance:** sistema original y ejemplo

## Contexto

El análisis con LLM es la parte cara: horas de GPU por periodo. La extracción y el informe tardan minutos, pero cambian a menudo: un filtro nuevo, un gráfico distinto, otra redacción. Si todo corre en un solo proceso, cualquier cambio en el informe obliga a repetir el análisis, y un corte a mitad de la noche obliga a empezar de cero.

## Decisión

El pipeline son tres programas independientes (extracción, análisis e informe). Cada uno lee el archivo que dejó el anterior y escribe el suyo. Ninguno llama a otro.

## Consecuencias

- Cambiar el informe no vuelve a analizar nada.
- Cada frontera es un archivo que se puede abrir, consultar y auditar.
- Una etapa que falla se reanuda sola, sin repetir las anteriores.
- A cambio, la orquestación es explícita (cron y scripts) y cada etapa tiene que validar su entrada: la etapa siguiente no puede asumir que la anterior terminó bien. [0004](0004-contratos-de-calidad-antes-de-publicar.md) formaliza esa validación.

## Alternativas descartadas

- **Un solo script de punta a punta:** simple de lanzar, pero acopla el costo del análisis a cada cambio del informe.
- **Un orquestador declarativo (Airflow, Dagster, Prefect) desde el inicio:** resuelve reintentos y linaje, pero para un servidor y una ejecución mensual por operación el costo de operarlo superaba el beneficio. Queda como paso de evolución en el [capítulo 2](../02-datos-y-almacenamiento.md#cómo-evolucionaría).
