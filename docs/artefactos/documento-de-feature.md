# Contratos de calidad entre capas

> Ejemplo de documento de feature escrito para este repositorio sobre una feature real del [ejemplo](../../ejemplo/calidad.py). Las citas del dueño del producto, las rutas de las tareas y las entradas del log son ilustrativas. Muestra la forma del contrato que leen todos los agentes; no es un documento del sistema original.

**Objetivo:** que una capa con defectos nunca llegue a la etapa siguiente ni a los tableros.
**Rama:** `feature/contratos-calidad` · **Entrega:** un PR.
**TDD:** desactivado (no hay configuración del proyecto que lo active). Verificación funcional: `uv run pytest`, `uv run --locked ruff check .` y el recorrido de las 3 etapas.
**Copia en memoria:** tema `odd/contratos-calidad/tasks`.

## Specs

- **S1 Detener ante violaciones.** Usuario: "si una capa sale mal, que el pipeline se detenga; prefiero no tener informe a tener uno equivocado".
- **S2 Conciliar el embudo.** Usuario: "lo que sale de la extracción tiene que cuadrar con lo que el SQL seleccionó".
- **S3 Reporte por corrida.** Usuario: "quiero ver qué se revisó, aunque todo haya pasado".
- **S4 Sin falsos positivos.** Usuario: "una corrida limpia no puede fallar por las reglas".
- **S5 Alcance.** Usuario: "no cambies el formato de las capas; los tableros ya las leen".

## Tasks

| ID | Specs | Ruta | Estado | Evidencia |
|---|---|---|---|---|
| T1 Motor de auditoría: reporte JSON atómico y detención con la lista de reglas violadas | S1, S3 | inline | hecho | prueba de compuerta: la capa no existe tras la violación y el reporte dice `ok: false` |
| T2 Contrato de la capa cruda (clave, dominio, embudo, coherencia de `analizable`) | S2, S4 | inline | hecho | 7 reglas; una corrupción dirigida por regla, todas detectadas |
| T3 Contratos de enriquecido, negocio y modelo estrella | S1, S4 | delegada | hecho | escritor B en paralelo con T4 (archivos disjuntos); 12 reglas |
| T4 Documentación de las reglas en el capítulo de datos | S3 | delegada | hecho | escritor A; revisado contra el código |
| T5 Verificación independiente | S1-S5 | delegada | hecho | verificador: S1-S4 cumplen; S5 cumple (`git diff` sin cambios de esquema en las capas) |
| T6 Mutaciones: debilitar cada regla y auditar después de escribir | S1, S4 | inline | hecho | 17 mutaciones, 17 detectadas |

## Criterios de aceptación

- Una violación en cualquier capa detiene la etapa **antes** de escribir la capa.
- Cada regla tiene al menos una prueba que la hace fallar.
- Una corrida limpia produce un reporte por capa y periodo, todos con `ok: true`.
- El esquema de las capas publicadas no cambia.

## Log

- L1 Pedido original: ver S1-S5 (citas literales).
- L2 Decisión: auditar en memoria y escribir solo si pasa (*write-audit-publish*), en lugar de escribir y borrar después. Un corte entre escribir y borrar dejaría una capa defectuosa publicada.
- L3 T2: la regla "fecha dentro del periodo" fallaba en la corrida limpia por la zona horaria del servidor. Se normalizó la fecha antes de comparar; S4 se volvió a verificar.
- L4 Cambio de requisito: el usuario pidió que el chequeo de huérfanos del modelo estrella pase al mismo reporte ("que todo quede en un solo lugar"). Se reescribe S3, se reabre T3 y se conserva el mensaje de error que ya esperaban los operadores.
- L5 Pendiente: el escaneo de privacidad de la CI no corrió en local porque la lista de términos vive en un secret. Queda registrado, no se marca como hecho.

**Próximo paso:** abrir el PR y revisar la corrida de la CI.
