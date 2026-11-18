#!/usr/bin/env bash
# Busca en los archivos versionados términos que no deben publicarse
# (nombres de la empresa, clientes, agentes, hosts, etc.).
#
# La lista NO vive en el repositorio, porque publicarla revelaría justo lo que
# protege. Se recibe por la variable TERMINOS_PROHIBIDOS (un término por línea):
#   - en GitHub Actions, desde el secret del mismo nombre;
#   - en local: TERMINOS_PROHIBIDOS="$(cat ~/terminos.txt)" .github/scripts/escanear_privacidad.sh
#
# Solo imprime archivo:línea, nunca el término ni la línea: los logs de un
# repositorio público también son públicos.
set -euo pipefail

if [[ -z "${TERMINOS_PROHIBIDOS:-}" ]]; then
  echo "TERMINOS_PROHIBIDOS no está definida: se omite el escaneo."
  exit 0
fi

lista="$(mktemp)"
trap 'rm -f "$lista"' EXIT
# Sin líneas vacías: un patrón vacío coincidiría con todo.
printf '%s\n' "$TERMINOS_PROHIBIDOS" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -v '^$' > "$lista" || true
echo "Términos a buscar: $(wc -l < "$lista")"

coincidencias="$(git grep -I -n -i -F -f "$lista" | cut -d: -f1,2 || true)"
if [[ -n "$coincidencias" ]]; then
  echo "Se encontraron términos confidenciales en:"
  echo "$coincidencias"
  exit 1
fi
echo "Sin coincidencias en $(git ls-files | wc -l) archivos versionados."
