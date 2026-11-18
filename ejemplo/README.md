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
├── checkpoints/
└── informes/
    ├── indicadores_<periodo>.json
    └── calidad/<capa>_<periodo>.json             # reporte de los contratos de cada capa
```
