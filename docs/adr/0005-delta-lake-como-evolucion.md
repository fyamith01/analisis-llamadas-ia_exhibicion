# 0005. Delta Lake como evolución, no como punto de partida

**Estado:** propuesta · **Alcance:** ejemplo ([`variante_delta.py`](../../ejemplo/variante_delta.py))

## Contexto

Con Parquet ([0002](0002-parquet-entre-etapas.md)), reanudar el análisis exige un checkpoint JSON propio. Comparar dos versiones de prompt obliga a guardar dos capas, y no hay transacciones entre archivos. El sistema original no usa Delta Lake. La pregunta es cuándo convendría.

## Decisión

Se demuestra la capa enriquecida como tabla Delta (delta-rs, sin Spark) en una variante del ejemplo, pero no se adopta como base:

- cada lote se confirma con un `MERGE` atómico condicionado por `version_analisis`;
- el prompt de cada commit queda en la metadata;
- la comparación entre prompts usa *time travel*.

## Consecuencias

- Reanudar no necesita checkpoint propio: un corte pierde como mucho el lote en curso.
- Un prompt nuevo actualiza en sitio y el anterior sigue consultable en el historial.
- Se paga en otro lado: cada `MERGE` reescribe los archivos de la partición (*copy-on-write*), los lotes pequeños fragmentan la tabla hasta un `OPTIMIZE`, y el historial ocupa disco hasta un `VACUUM`.
- Criterio de adopción: varios procesos escribiendo la misma capa, auditorías que piden "qué decía el informe con el prompt anterior", o capas en almacenamiento de objetos compartido.

## Alternativas descartadas

- **Adoptarlo desde el inicio:** con un servidor, un escritor y archivos de un mes, el checkpoint atómico resuelve lo mismo con menos piezas.
- **Apache Iceberg:** cumple un papel equivalente (transacciones, `MERGE`, *time travel*). La demostración usa Delta; el criterio de adopción sería el mismo con cualquiera de los dos.
- **Una fila por versión de prompt:** evita reescribir, pero duplica la tabla y obliga a filtrar la versión vigente en cada consulta.
