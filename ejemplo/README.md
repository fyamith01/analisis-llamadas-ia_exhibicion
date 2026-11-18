# Ejemplo ejecutable

Reproduce el patrón del sistema real con **datos 100 % sintéticos**: 3 etapas comunicadas por Parquet, capas crudo → enriquecido → negocio, checkpoints reanudables y consultas SQL analíticas. El LLM está simulado con reglas, así que no necesitas GPU, Ollama ni bases de datos.

## Quick path

Requisito: [uv](https://docs.astral.sh/uv/). Cada script declara sus dependencias (PEP 723) y `uv` las instala solo.

```bash
cd ejemplo
uv run generar_datos.py   # 1. fuente transaccional sintética (DuckDB)
uv run pipeline.py        # 2. extracción → análisis → informe
uv run consultas.py       # 3. SQL analítico sobre los Parquet
```

Resultado esperado: `datos/` con las capas particionadas y `datos/informes/indicadores_<periodo>.json`.

## Prueba la reanudación

```bash
rm -rf datos/checkpoints datos/enriquecido
uv run pipeline.py --etapa 2 --interrumpir 40   # se corta a las 40 filas y guarda el avance
uv run pipeline.py --etapa 2                    # continúa donde quedó
```

Si cambias `PROMPT` o `MODELO` en `pipeline.py`, cambia la versión del análisis y el checkpoint viejo se descarta solo.

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
| CTE, `RANK`, `LAG`, `QUALIFY`, `UNPIVOT`, `FILTER` | `consultas.py` |

## Estructura generada

```text
datos/
├── fuente/fuente.duckdb                         # simula los sistemas transaccionales
├── crudo/conversaciones/operacion=demo/periodo=2026-10/parte-0.parquet
├── enriquecido/conversaciones/operacion=demo/periodo=2026-10/parte-0.parquet
├── negocio/
│   ├── evaluaciones/operacion=demo/periodo=…/
│   └── ranking_agentes/operacion=demo/periodo=…/
├── checkpoints/
└── informes/indicadores_<periodo>.json
```
