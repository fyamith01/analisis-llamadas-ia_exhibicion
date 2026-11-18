# 6. Lecciones aprendidas

**Cada fila de esta tabla es un error real que nos costó horas de GPU o una cifra equivocada, y la regla que dejó.** Casi todos fallaron en silencio: el proceso terminaba con éxito y el dato estaba mal.

## Datos y pipeline

| Qué pasó | Por qué fue grave | Regla |
|---|---|---|
| Ante un archivo de entrada faltante, una etapa creó un DataFrame de ejemplo con 3 filas inventadas | Se generó un informe "real" con datos ficticios y código de salida 0 | **Fallar en voz alta.** Si falta la entrada, `SystemExit`; nunca hay datos de relleno |
| Una errata en la ruta de salida de una etapa no coincidía con la ruta de entrada de la siguiente | La siguiente etapa no encontraba el archivo y caía en el caso anterior | Las rutas entre etapas se construyen con la misma función |
| La consulta de chats ordenaba por remitente y luego por mensaje | Los diálogos quedaban desordenados y el modelo leía conversaciones sin sentido | Ordenar siempre por marca de tiempo; la huella depende del orden |
| Una consulta no filtraba por estado de la transcripción | Llegaban filas vacías al análisis | El filtro de estado es obligatorio y está documentado en el embudo |
| El checkpoint se indexaba por posición | Al filtrar filas, se reutilizaron resultados de otras conversaciones | Huella de la entrada en el checkpoint; la mejora pendiente es indexar por contenido |

## IA

| Qué pasó | Por qué fue grave | Regla |
|---|---|---|
| Ventana de contexto sin fijar | Cerca del 40 % de las filas de una corrida perdieron las instrucciones del sistema | Fijar `num_ctx` y auditar `tokens_prompt` en cada fila |
| El checkpoint no incluía la versión del prompt | Al cambiar el prompt, el "nuevo" análisis devolvía el viejo en segundos | Versión = hash de prompt + esquema + modelo |
| El modelo no formaba parte de la huella | Un piloto con otro modelo habría reutilizado las respuestas del anterior | El modelo va en la huella y en el nombre del archivo |
| La columna de errores incluía la prosa del modelo | Se reportaron diez veces más errores de los reales | Una función separa los errores reales del texto del modelo |
| Una categoría salió en casi todas las filas | Parecía un hallazgo y era un colapso del modelo | Alerta automática si una categoría pasa del 40 % |
| Un ejemplo literal en el prompt | El modelo lo copió en más de la mitad de las respuestas | Sin ejemplos literales |

## Operación

| Qué pasó | Regla |
|---|---|
| Un log parecía colgado durante horas | `python -u` con `tee` |
| Una conexión a base de datos quedaba abierta desde que se importaba el módulo | Conexiones perezosas, creadas en el primer uso |
| Con un `.env` incompleto, `import main` fallaba entero | Validar credenciales al conectar, no al importar |
| El servidor se congeló varias veces en una corrida larga | Checkpoints frecuentes, concurrencia conservadora y journal persistente. La causa raíz sigue sin confirmar y así se reporta |

## Trabajo con agentes de IA

| Qué pasó | Regla |
|---|---|
| El agente afirmó que una configuración no estaba activa comparando corridas que no eran comparables | **Medir, no inferir.** Las hipótesis se marcan como hipótesis y se entrega un script para medirlas |
| Ediciones automáticas con reemplazos de texto fallaron en silencio | Verificar cada edición; compilar e importar después de cada cambio |
| En sesiones largas se perdían requisitos | Specs con citas literales del pedido en el documento de feature |
| Una "mejora" tocó código que otras operaciones compartían | Separar los errores preexistentes de los propios con `git diff`; los cambios a código compartido se tratan como de alto riesgo |

## Lo que haría distinto desde el inicio

1. Un módulo común desde la primera operación, en vez de copias adaptadas.
2. Particionado y nombres de archivo estandarizados desde el día uno.
3. Un conjunto de prueba etiquetado por humanos antes de elegir el primer modelo.
4. Un orquestador de flujos de datos en lugar de `cron` + `tmux` cuando pasamos de cinco operaciones.

[← Volver al inicio](../README.md)
