# 0002. Parquet entre etapas en lugar de una base de datos

**Estado:** aceptada · **Alcance:** sistema original y ejemplo

## Contexto

Las etapas necesitan un contrato de datos. Cada fila trae miles de caracteres de texto y decenas de columnas de resultados. Las bases de producción son transaccionales y no deben recibir cargas analíticas. Los consumidores son pandas, DuckDB y herramientas de BI, y el volumen de un periodo cabe holgado en un disco.

## Decisión

Cada capa (crudo, enriquecido y negocio) es un conjunto de archivos Parquet particionados estilo Hive (`operacion=…/periodo=…/`), escritos de forma atómica (`tmp` + `fsync` + `replace`).

## Consecuencias

- Lectura columnar: el informe lee las calificaciones sin cargar el texto.
- El esquema viaja con el archivo: tipos, nulos y booleanos llegan intactos.
- Los archivos se consultan con SQL (DuckDB) sin servidor de base de datos.
- A cambio, no hay transacciones entre archivos ni actualizaciones por fila. Reanudar exige un checkpoint propio y la concurrencia de escritores no está resuelta. [0005](0005-delta-lake-como-evolucion.md) evalúa cuándo pasar a un formato de tabla transaccional.

## Alternativas descartadas

- **CSV:** pierde tipos, no comprime por columna y rompe con textos multilínea.
- **Una base de datos analítica intermedia:** agrega un servicio que operar, respaldar y asegurar, para un volumen que un archivo resuelve.
- **Escribir los resultados en las bases de producción:** mezcla la carga analítica con la transaccional y complica auditar qué versión del análisis produjo cada fila.
