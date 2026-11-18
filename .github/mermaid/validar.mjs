// Valida los bloques ```mermaid de los archivos Markdown recibidos como argumentos.
//
// 1. Sintaxis: mermaid.parse(), el mismo parser que usa el renderizador de GitHub.
// 2. Gantt: el parser acepta duraciones que luego no se pueden calcular (por ejemplo
//    "5h30m") y la barra se dibuja con longitud cero. Por eso se calculan las fechas
//    de cada tarea y se exige que las que no son hitos terminen después de empezar.
//
// Uso: node .github/mermaid/validar.mjs README.md docs/*.md
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body></body>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
const { default: mermaid } = await import('mermaid');
mermaid.initialize({ startOnLoad: false });

function* bloques(archivo) {
  const lineas = readFileSync(archivo, 'utf8').split('\n');
  for (let i = 0; i < lineas.length; i++) {
    if (lineas[i].trim() !== '```mermaid') continue;
    const inicio = i + 1;
    const cuerpo = [];
    while (++i < lineas.length && lineas[i].trim() !== '```') cuerpo.push(lineas[i]);
    yield { linea: inicio, codigo: cuerpo.join('\n') };
  }
}

async function problemasDeGantt(codigo) {
  const diagrama = await mermaid.mermaidAPI.getDiagramFromText(codigo);
  return diagrama.db
    .getTasks()
    .filter((t) => !t.milestone && !(t.endTime > t.startTime))
    .map((t) => `la tarea "${t.task.trim()}" no tiene duración válida`);
}

let total = 0;
let fallos = 0;
for (const archivo of process.argv.slice(2)) {
  for (const { linea, codigo } of bloques(archivo)) {
    total++;
    let problemas = [];
    let tipo = '?';
    try {
      tipo = (await mermaid.parse(codigo)).diagramType;
      if (tipo === 'gantt') problemas = await problemasDeGantt(codigo);
    } catch (error) {
      problemas = [String(error.message ?? error).split('\n').slice(0, 3).join(' | ')];
    }
    if (problemas.length) {
      fallos++;
      for (const p of problemas) console.log(`FALLA ${archivo}:${linea} (${tipo}) ${p}`);
    } else {
      console.log(`ok    ${archivo}:${linea} (${tipo})`);
    }
  }
}
console.log(`\n${total - fallos} de ${total} diagramas válidos`);
process.exit(fallos || total === 0 ? 1 : 0);
