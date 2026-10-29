#!/usr/bin/env bash
# Etapa 0 del robot: parada inmediata, sin confirmación previa.
#
# Uso: ev3-parar.sh [--url URL]
#
# POST /detener_ejecucion (hasta 3 intentos, 0,2 s entre ellos, hasta recibir ok:true) y
# luego GET /estado_ejecucion hasta ver ejecutando:false dos veces seguidas, como mucho
# 10 s. Cada petición, 2 s como máximo. No consulta /status: funciona aunque /status falle.
# Imprime los milisegundos desde el primer POST hasta la confirmación.
#
# Códigos de salida: 0 solo si la parada quedó confirmada; 1 si no; 2 uso o URL.

set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/ev3-comun.sh"

url_arg=""
while (( $# > 0 )); do
    case $1 in
        --url) (( $# >= 2 )) || { echo "Uso: $(basename "$0") [--url URL]" >&2; exit 2; }; url_arg=$2; shift ;;
        *) error "argumento desconocido: $1"; echo "Uso: $(basename "$0") [--url URL]" >&2; exit 2 ;;
    esac
    shift
done
resolver_url "$url_arg" || exit 2
parar_y_confirmar
