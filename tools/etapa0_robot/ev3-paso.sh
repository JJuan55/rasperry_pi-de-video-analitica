#!/usr/bin/env bash
# Etapa 0 del robot: UN paso acotado, sin gestos.
#
# Uso: ev3-paso.sh {avanzar|izquierda|derecha} [--grados N] [--potencia P] [--url URL] [--ensayo]
#
#   avanzar:   outC +N, outD +N      (C = motorIzq = rueda izquierda)
#   derecha:   outC +N, outD -N      (D = motorDer = rueda derecha)
#   izquierda: outC -N, outD +N
#
# Topes duros: grados 1..180 (90 por defecto), potencia 1..30 (20 por defecto). Fuera de
# rango es un error, no se recorta. Antes de mover: GET /status (batería, outC y outD
# conectados) y GET /estado_ejecucion (si ya ejecuta, no envía); confirmación escrita SI;
# y otra vez /estado_ejecucion justo antes de enviar. Si el POST no recibe respuesta NO se
# reintenta: sin respuesta no significa "no ejecutado".
#
# --ensayo: imprime lo que enviaría y termina, sin abrir ninguna conexión (no necesita URL).
#
# Códigos de salida: 0 paso aceptado (ok:true, totalPasos:1) o ensayo; 1 el robot no
# aceptó, no respondió o no pasó una comprobación; 2 uso o URL; 3 sin confirmación SI.

set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/ev3-comun.sh"

uso() { echo "Uso: $(basename "$0") {avanzar|izquierda|derecha} [--grados N] [--potencia P] [--url URL] [--ensayo]" >&2; }

direccion="" grados=90 potencia=20 url_arg="" ensayo=no
while (( $# > 0 )); do
    case $1 in
        avanzar | izquierda | derecha)
            [[ -n "$direccion" ]] && { error "solo una dirección por llamada (un paso)."; uso; exit 2; }
            direccion=$1 ;;
        --grados) (( $# >= 2 )) || { uso; exit 2; }; grados=$2; shift ;;
        --potencia) (( $# >= 2 )) || { uso; exit 2; }; potencia=$2; shift ;;
        --url) (( $# >= 2 )) || { uso; exit 2; }; url_arg=$2; shift ;;
        --ensayo) ensayo=si ;;
        *) error "argumento desconocido: $1"; uso; exit 2 ;;
    esac
    shift
done
[[ -n "$direccion" ]] || { error "falta la dirección."; uso; exit 2; }
validar_entero grados "$grados" "$GRADOS_MIN" "$GRADOS_MAX" || exit 2
validar_entero potencia "$potencia" "$POTENCIA_MIN" "$POTENCIA_MAX" || exit 2
grados=$(( 10#$grados ))
potencia=$(( 10#$potencia ))

case $direccion in
    avanzar) g_izq=$grados; g_der=$grados ;;
    derecha) g_izq=$grados; g_der=$(( -grados )) ;;
    izquierda) g_izq=$(( -grados )); g_der=$grados ;;
esac
cuerpo=$(printf '{"pasos": [{"motorIzq": "outC", "motorDer": "outD", "gradosIzq": %d, "gradosDer": %d, "potencia": %d}]}' \
    "$g_izq" "$g_der" "$potencia")

if [[ "$ensayo" == si ]]; then
    echo "ENSAYO (no se abre ninguna conexión). Haría, en este orden:"
    echo "  GET /status            -> batería; outC y outD deben estar conectados"
    echo "  GET /estado_ejecucion  -> debe decir ejecutando:false"
    echo "  pedir la confirmación: $PREGUNTA_SEGURIDAD"
    echo "  GET /estado_ejecucion  -> otra vez, justo antes de enviar"
    echo "  POST /ejecutar_ruta $cuerpo"
    echo "  y exigiría la respuesta ok:true con totalPasos:1"
    exit 0
fi

resolver_url "$url_arg" || exit 2
echo "Paso: $direccion, $grados°, potencia $potencia (URL tomada de $URL_ORIGEN)."
comprobar_antes_de_mover || exit 1
pedir_confirmacion || exit 3
comprobar_libre || exit 1

echo "[$(hora)] POST /ejecutar_ruta $cuerpo"
MAX_TIME=5 peticion POST /ejecutar_ruta "$cuerpo"
echo "[$(hora)] Respuesta: ${RESP_FALLO:-HTTP $RESP_CODIGO $RESP_CUERPO}"

if [[ "$RESP_CODIGO" == 000 ]]; then
    echo "SIN RESPUESTA AL PASO. NO SE REINTENTA: sin respuesta no significa \"no ejecutado\"."
    MAX_TIME=2 peticion GET /estado_ejecucion
    echo "[$(hora)] GET /estado_ejecucion: ${RESP_FALLO:-HTTP $RESP_CODIGO $RESP_CUERPO}"
    echo "Mira el robot. Para detenerlo: ev3-parar.sh"
    exit 1
fi
if [[ "$RESP_CODIGO" == 409 ]]; then
    echo "409: el robot dice que ya hay una ruta en ejecución. No se envió el paso y no se reintenta."
    exit 1
fi
if [[ "$RESP_CODIGO" != 200 ]]; then
    error "respuesta HTTP $RESP_CODIGO inesperada al paso."
    exit 1
fi
if ! campos=$(json_campos "$RESP_CUERPO" ok totalPasos); then
    error "la respuesta al paso no es JSON válido."
    exit 1
fi
{ read -r ok; read -r total; } <<< "$campos"
if [[ "$ok" != true || "$total" != 1 ]]; then
    error "respuesta ok=$ok, totalPasos=$total; se esperaba ok:true y totalPasos:1. Según el contrato, un JSON mal formado devuelve ok con 0 pasos: se trata como fallo."
    exit 1
fi
echo "Paso aceptado (ok:true, totalPasos:1). Para detenerlo: ev3-parar.sh"
