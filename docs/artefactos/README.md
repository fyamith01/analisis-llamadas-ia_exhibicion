# Artefactos del desarrollo multiagente

Ejemplos de los archivos con los que se coordinó el equipo de agentes descrito en el [capítulo 4](../04-desarrollo-multiagente.md). Están **escritos desde cero para este repositorio**: no son los prompts, los subagentes ni los documentos del sistema original.

| Archivo | Qué muestra |
|---|---|
| [`agentes/explorador.md`](agentes/explorador.md) | Subagente de solo lectura (`Read`, `Grep`, `Glob`): responde una pregunta con un mapa de `ruta:línea` |
| [`agentes/verificador.md`](agentes/verificador.md) | Verificador independiente: ejecuta pruebas con `Bash`, pero no tiene herramientas de edición, y devuelve un veredicto por spec |
| [`documento-de-feature.md`](documento-de-feature.md) | El contrato que leen todos los agentes: specs con citas literales, tareas con ruta y evidencia, criterios de aceptación y log |

## Cómo usar los subagentes

Son definiciones de subagente de [Claude Code](https://code.claude.com/docs/en/sub-agents): un frontmatter con nombre, descripción y herramientas permitidas, y el prompt de sistema en el cuerpo. Para activarlos en un proyecto, cópialos a `.claude/agents/`:

```bash
mkdir -p .claude/agents
cp docs/artefactos/agentes/*.md .claude/agents/
```

La lista `tools` es la restricción principal: un agente sin `Edit` ni `Write` no puede modificar archivos con esas herramientas. `Bash` sí podría escribir, así que el verificador lo recibe con la instrucción explícita de solo ejecutar y leer. Si se quiere una garantía más fuerte, se agregan reglas de permisos en la configuración del proyecto.

Una prueba de [`ejemplo/tests/test_artefactos.py`](../../ejemplo/tests/test_artefactos.py) hace cumplir estas restricciones: ningún agente de esta carpeta puede declarar herramientas de edición, y el documento de feature debe tener sus secciones.
