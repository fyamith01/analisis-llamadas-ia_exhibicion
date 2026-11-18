# Ejemplo ejecutable

Reproduce el patrón del sistema real con **datos 100 % sintéticos**: 3 etapas comunicadas por Parquet, capas crudo → enriquecido → negocio con contratos de calidad entre ellas, un modelo estrella con dimensión SCD tipo 2, checkpoints reanudables y consultas SQL analíticas. El LLM está simulado con reglas, así que no necesitas GPU, Ollama ni bases de datos.

## Quick path

Requisito: [uv](https://docs.astral.sh/uv/). Cada script declara sus dependencias (PEP 723) y `uv` las instala solo.

```bash
cd ejemplo
uv run generar_datos.py   # 1. fuente transaccional sintética (DuckDB)
uv run pipeline.py        # 2. extracción → análisis → informe
uv run consultas.py       # 3. SQL analítico sobre los Parquet
```

Resultado esperado: `datos/` con las capas particionadas, `datos/informes/indicadores_<periodo>.json` y un reporte de calidad por capa y periodo en `datos/informes/calidad/`.

## Prueba la reanudación

```bash
rm -rf datos/checkpoints datos/enriquecido
uv run pipeline.py --etapa 2 --interrumpir 40   # se corta a las 40 filas y guarda el avance
uv run pipeline.py --etapa 2                    # continúa donde quedó
```

Si cambias `PROMPT` o `MODELO` en `pipeline.py`, cambia la versión del análisis y el checkpoint viejo se descarta solo.

## Variante con Delta Lake (evolución)

`variante_delta.py` muestra cómo **evolucionaría** la capa enriquecida si fuera una tabla [Delta Lake](https://delta.io/) (con [delta-rs](https://delta-io.github.io/delta-rs/), sin Spark ni JVM). No es lo que usa el sistema real: allí la capa es Parquet con un checkpoint JSON.

```bash
uv run pipeline.py --etapa 1                      # la variante parte de la capa cruda
uv run variante_delta.py --prompt v1 --interrumpir 40
uv run variante_delta.py                          # reanuda v1, aplica el prompt v2 y compara
```

| Con Parquet + checkpoint | Con Delta Lake |
|---|---|
| Checkpoint JSON cada 5 % con escritura atómica casera | Cada lote se confirma con un `MERGE` transaccional; un corte pierde como mucho el lote en curso |
| La versión del análisis invalida el checkpoint completo | El `MERGE` solo reescribe las filas cuya `version_analisis` cambió; repetir la misma versión no crea commits |
| Comparar dos prompts exige guardar dos capas | **Time travel**: la tabla tiene una fila por conversación y el resultado del prompt anterior sigue en una versión anterior (`load_as_version`) |
| El esquema se valida en Pydantic | Además, la tabla rechaza escrituras con otro esquema |

Cada commit lleva el prompt en su metadata, así que la comparación busca en el historial la última versión de cada prompt y la consulta con DuckDB. Al final de cada corrida, `OPTIMIZE` compacta los archivos pequeños que deja cada lote. Los archivos viejos siguen ahí para el *time travel* hasta que un `VACUUM` los borre: ese es el costo de retención que hay que decidir.

## Auditoría multiagente

`multiagente.py` toma una muestra estratificada por canal y la revisa con tres roles: un **evaluador** que aplica la matriz, un **juez a ciegas** que solo ve la transcripción y un **verificador de citas** determinista. Mide el acuerdo con el **kappa de Cohen** y deja los desacuerdos en una cola de revisión humana.

```bash
uv run pipeline.py --etapa 1      # la muestra sale de la capa cruda
uv run multiagente.py             # todo simulado: sin GPU ni claves
```

Cada rol puede usar un modelo real con la misma interfaz de salida estructurada. Los SDK son dependencias opcionales:

```bash
uv run --with ollama multiagente.py --evaluador ollama --modelo-ollama qwen2.5:7b   # OLLAMA_HOST si no es local
uv run --with anthropic multiagente.py --juez claude                              # ANTHROPIC_API_KEY o `ant auth login`
uv run --with ollama --with anthropic multiagente.py --evaluador ollama --juez claude
```

Con la API de Claude, el juez usa por defecto `claude-opus-5-5` con salida estructurada (`messages.parse` con el modelo Pydantic) y respaldo automático si un clasificador de seguridad rechaza la petición. Tiene costo por token y envía las transcripciones a la API: con los datos sintéticos del ejemplo no hay problema, pero con datos reales hace falta un acuerdo de tratamiento de datos.

Salidas: `datos/multiagente/auditoria.parquet`, `revision_humana.parquet` y `resumen.json` (kappa y nivel por campo, citas verificadas y conversaciones a revisión).

## Pruebas

```bash
cd ejemplo
uv run pytest
```

`pyproject.toml` solo define el entorno de pruebas; los scripts siguen declarando sus dependencias en línea. Las pruebas trabajan en directorios temporales y no tocan `datos/`. Cubren lo que en el sistema real costó incidentes:

| Prueba | Qué protege |
|---|---|
| Escritura atómica | Que un corte nunca deje un Parquet a medias ni archivos temporales |
| Reconstrucción de chats | Que los mensajes desordenados se unan por marca de tiempo |
| Embudo | Que fallidas y cortas se excluyan, y que los textos breves se marquen sin borrarse |
| Reanudación | Que interrumpir y reanudar dé lo mismo que una corrida completa **sin volver a llamar al modelo** por las filas hechas |
| Versión del análisis | Que cambiar el modelo descarte el checkpoint viejo |
| Esquema | Notas dentro de la matriz, motivos válidos y razonamiento antes que las notas |
| Variante Delta Lake | Que v1 dé lo mismo que la capa Parquet, que reanudar solo analice lo no confirmado, que repetir la versión no cree commits, que el prompt v2 actualice en sitio y el v1 siga accesible por *time travel*, y que un lote inválido no se confirme |
| Multiagente | Que el juez no reciba nada del evaluador, el kappa contra casos calculados a mano, cada falla del verificador, cada causa de revisión por separado y los adaptadores de Ollama y Claude con clientes falsos (sin red) |
| Contratos de calidad | Que cada regla detecte su defecto (una corrupción dirigida por regla) y que una violación detenga la etapa **sin publicar la capa** |
| Modelo estrella | Grano único, integridad referencial, vigencias SCD2 contiguas, versión vigente por fecha, calendario completo y conciliación con pandas |

Se verificaron introduciendo errores a propósito en el pipeline (mutaciones): cada uno hace fallar al menos una prueba.

## Qué mirar en el código

| Concepto | Dónde |
|---|---|
| Reconstrucción de chats ordenando por marca de tiempo | `SQL_CHAT` en `pipeline.py` |
| Embudo de selección | `etapa1_extraccion` |
| Particionado estilo Hive | `ruta_capa` |
| Escritura atómica (`tmp` + `fsync` + `replace`) | `escribir_atomico` |
| Esquema con el razonamiento antes de la etiqueta | clase `Evaluacion` |
| Huella de entrada y versión del análisis | `huella_entrada`, `version_analisis` |
| Cita literal por número de fragmento | final de `etapa2_analisis` |
| Pesos aplicados fuera del modelo y alerta de categoría dominante | `etapa3_informe` |
| Contratos de calidad entre capas: auditar antes de publicar | `calidad.py` y las llamadas a `auditar` en `pipeline.py` |
| `MERGE` por lotes, metadata de commit, `OPTIMIZE` y *time travel* con Delta Lake | `variante_delta.py` |
| Juez a ciegas, verificador de citas, kappa de Cohen y proveedores intercambiables | `multiagente.py` |
| Modelo estrella: grano conversación × parámetro | `construir_modelo_estrella`, `sql_hecho_evaluacion` |
| Dimensión SCD tipo 2 con `LEAD()` y búsqueda de clave sustituta por fecha | `SQL_DIM_AGENTE`, `sql_hecho_evaluacion` |
| Calendario completo con clave `AAAAMMDD` | `SQL_DIM_FECHA` |
| CTE, `RANK`, `LAG`, `QUALIFY`, `UNPIVOT`, `FILTER` | `consultas.py` |
| SCD2 frente a sobrescribir (SCD1) | última consulta de `consultas.py` |

## Estructura generada

```text
datos/
├── fuente/fuente.duckdb                         # simula los sistemas transaccionales
├── crudo/
│   ├── conversaciones/operacion=demo/periodo=2026-10/parte-0.parquet
│   └── asignaciones_agente/parte-0.parquet       # bitácora de cambios de equipo
├── enriquecido/conversaciones/operacion=demo/periodo=2026-10/parte-0.parquet
├── negocio/
│   ├── evaluaciones/operacion=demo/periodo=…/
│   ├── ranking_agentes/operacion=demo/periodo=…/
│   └── modelo/                                   # estrella para BI
│       ├── hecho_evaluacion/operacion=demo/periodo=…/
│       └── dim_agente/ · dim_fecha/ · dim_parametro/ · dim_operacion/
├── delta/enriquecido/                            # variante Delta: _delta_log/ + periodo=…/
├── multiagente/                                  # auditoría, cola de revisión humana y resumen
├── checkpoints/
└── informes/
    ├── indicadores_<periodo>.json
    └── calidad/<capa>_<periodo>.json             # reporte de los contratos de cada capa
```
