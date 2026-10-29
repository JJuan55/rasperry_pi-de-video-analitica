#!/usr/bin/env bash
# Etapa 0 del robot: parada en pleno movimiento, sin gestos.
#
# Uso: ev3-prueba-parada.sh [--pasos N] [--url URL]
#
# Con la misma comprobación y la misma confirmación SI que ev3-paso.sh, envía UNA ruta de
# N pasos de 180° hacia delante a potencia 20 (N de 1 a 4, 4 por defecto: tope 4×180° por
# rueda). Si el robot la acepta, espera 1,0 s y para con el mismo mecanismo que
# ev3-parar.sh. Si no la acepta o no responde, para enseguida (por si se mueve).
#
# Comprobación objetiva con GET /status (velocidad y posición de outC y outD): justo antes
# de parar; 0,3 s y 1,0 s después del primer POST /detener_ejecucion; al terminar el
# mecanismo de parada (confirmada o no); y 3 s después de eso.
#   PARÓ en X ms  = parada confirmada por /estado_ejecucion, velocidades 0 al terminar y
#                   posiciones sin cambiar más de TOL_GRADOS en los 3 s siguientes (no
#                   reanuda). X = del primer POST /detener_ejecucion a la confirmación.
#                   Se indica además si X cumple el objetivo de la hoja (< 1000 ms).
#   NO PARÓ       = cualquier otra cosa, con el motivo.
#   PRUEBA NO VÁLIDA = la ruta no quedó aceptada (ok:true y totalPasos:N); igual se para.
#
# Registro con hora de cada evento en ~/robot_pruebas/ (fuera del repo), sin la URL.
# Códigos de salida: 0 PARÓ; 1 NO PARÓ, prueba no válida o comprobación fallida; 2 uso o
# URL; 3 sin confirmación SI.

set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/ev3-comun.sh"

GRADOS=180
POTENCIA=20
TOL_GRADOS=5  # margen de posición para "no se movió" (ruido del encoder); ajustar con el robot

uso() { echo "Uso: $(basename "$0") [--pasos N] [--url URL]" >&2; }

pasos=4 url_arg=""
while (( $# > 0 )); do
    case $1 in
        --pasos) (( $# >= 2 )) || { uso; exit 2; }; pasos=$2; shift ;;
        --url) (( $# >= 2 )) || { uso; exit 2; }; url_arg=$2; shift ;;
        *) error "argumento desconocido: $1"; uso; exit 2 ;;
    esac
    shift
done
validar_entero pasos "$pasos" 1 4 || exit 2
pasos=$(( 10#$pasos ))
resolver_url "$url_arg" || exit 2

# Lee las lecturas de /status tomadas desde la parada, en orden ("etiqueta vC vD pC pD",
# con ? si falta), e imprime los motivos de fallo, uno por línea (nada si todo bien):
#   REANUDÓ        si una lectura vio las dos ruedas quietas y otra posterior las ve girar
#                  o moverse más de TOL_GRADOS;
#   no quedaron quietas  si al terminar la parada o 3 s después alguna rueda gira, o la
#                  posición cambia más de TOL_GRADOS entre esas dos lecturas;
#   faltan lecturas      si no hay esas dos lecturas.
evaluar_lecturas() {
    python3 -c '
import sys
tol = float(sys.argv[1])
L = [l.split() for l in sys.stdin if l.strip()]
for r in L:
    r[0] = r[0].replace("_", " ")
def n(x):
    try:
        return float(x)
    except ValueError:
        return None
def quieta(r):
    return n(r[1]) == 0 and n(r[2]) == 0
def mueve(a, b):
    pa, pb = [n(x) for x in a[3:5]], [n(x) for x in b[3:5]]
    if None in pa + pb:
        return False
    return any(abs(x - y) > tol for x, y in zip(pa, pb))
for i, r in enumerate(L):
    if quieta(r):
        for s in L[i + 1:]:
            if (n(s[1]) not in (0, None) or n(s[2]) not in (0, None)) or mueve(r, s):
                print(f"REANUDÓ: quietas en «{r[0]}» (outC {r[3]}, outD {r[4]}) y luego en «{s[0]}» velocidad outC {s[1]}, outD {s[2]}, posición outC {s[3]}, outD {s[4]}")
                sys.exit(0)
fin, tras = L[-2], L[-1]
if None in [n(x) for x in fin[1:] + tras[1:]]:
    print("faltan lecturas de /status al terminar la parada o 3 s después: no se puede comprobar que las ruedas pararon")
elif not (quieta(fin) and quieta(tras)) or mueve(fin, tras):
    print(f"las ruedas no quedaron quietas: al terminar velocidad outC {fin[1]}, outD {fin[2]}; 3 s después posición outC {fin[3]} -> {tras[3]}, outD {fin[4]} -> {tras[4]}")
' "$1"
}

# Lee /status, lo muestra y lo guarda en el arreglo lecturas de principal().
lectura() {
    if leer_status; then
        mostrar_motores "$1:"
        lecturas+=("${1// /_} $C_VEL $D_VEL $C_POS $D_POS")
    else
        echo "[$(hora)] $1: /status sin lectura ($RESP_FALLO)"
        lecturas+=("${1// /_} ? ? ? ?")
    fi
}

principal() {
    local paso_json cuerpo ruta_ok=no problema_ruta="" campos ok total t_parada res motivos=()
    local lecturas=() m

    echo "Prueba de parada en movimiento: $(date '+%F %T')"
    echo "Ruta: $pasos paso(s) de ${GRADOS}° hacia delante a potencia $POTENCIA. URL tomada de $URL_ORIGEN."
    comprobar_antes_de_mover || return 1
    pedir_confirmacion || return 3
    comprobar_libre || return 1

    paso_json=$(printf '{"motorIzq": "outC", "motorDer": "outD", "gradosIzq": %d, "gradosDer": %d, "potencia": %d}' \
        "$GRADOS" "$GRADOS" "$POTENCIA")
    cuerpo='{"pasos": ['$paso_json
    for (( i = 1; i < pasos; i++ )); do cuerpo+=", $paso_json"; done
    cuerpo+=']}'

    echo "[$(hora)] POST /ejecutar_ruta ($pasos paso(s))"
    MAX_TIME=5 peticion POST /ejecutar_ruta "$cuerpo"
    echo "[$(hora)] Respuesta: ${RESP_FALLO:-HTTP $RESP_CODIGO $RESP_CUERPO}"
    if [[ "$RESP_CODIGO" == 200 ]] && campos=$(json_campos "$RESP_CUERPO" ok totalPasos); then
        { read -r ok; read -r total; } <<< "$campos"
        if [[ "$ok" == true && "$total" == "$pasos" ]]; then
            ruta_ok=si
        else
            problema_ruta="la ruta no se aceptó bien: ok=$ok, totalPasos=$total (se esperaba ok:true y totalPasos:$pasos)"
        fi
    elif [[ "$RESP_CODIGO" == 000 ]]; then
        problema_ruta="la ruta no recibió respuesta (no se reintenta; puede haberse ejecutado)"
    elif [[ "$RESP_CODIGO" == 409 ]]; then
        problema_ruta="409: el robot ya ejecutaba otra ruta"
    else
        problema_ruta="respuesta HTTP $RESP_CODIGO inesperada a la ruta"
    fi

    if [[ "$ruta_ok" == si ]]; then
        echo "[$(hora)] Ruta aceptada. Espera de 1,0 s en movimiento."
        sleep 1.0
    else
        echo "[$(hora)] La ruta no quedó confirmada: se para ya, por si el robot se mueve."
    fi

    if leer_status; then mostrar_motores "Antes de parar:"; else echo "[$(hora)] Antes de parar: /status sin lectura ($RESP_FALLO)"; fi

    res=$(mktemp)
    t_parada=$(ahora_ms)
    ( parar_y_confirmar; echo "$? ${PARADA_MS:-}" > "$res" ) &
    local pid=$!

    dormir_hasta $(( t_parada + 300 ))
    lectura "+0,3 s"
    dormir_hasta $(( t_parada + 1000 ))
    lectura "+1,0 s"
    wait "$pid"
    local rc_parada ms_parada t_fin
    read -r rc_parada ms_parada < "$res"
    rm -f "$res"

    t_fin=$(ahora_ms)
    lectura "al terminar la parada"
    echo "[$(hora)] Espera de 3 s más para comprobar que no reanuda."
    dormir_hasta $(( t_fin + 3000 ))
    lectura "3 s después"

    [[ "$rc_parada" == 0 ]] || motivos+=("/estado_ejecucion no confirmó la parada en 10 s")
    m=$(printf '%s\n' "${lecturas[@]}" | evaluar_lecturas "$TOL_GRADOS")
    if [[ -n "$m" ]]; then
        motivos+=("$m")
    else
        echo "[$(hora)] Ruedas quietas al terminar y sin moverse en los 3 s siguientes (±${TOL_GRADOS}°): no reanuda."
    fi

    if [[ -n "$problema_ruta" ]]; then
        echo "RESULTADO: PRUEBA NO VÁLIDA: $problema_ruta"
        if (( ${#motivos[@]} == 0 )); then
            echo "  La parada sí quedó confirmada y comprobada ($ms_parada ms), pero sin movimiento confirmado no mide nada."
            return 1
        fi
        echo "  Y además la parada NO quedó comprobada:"
    elif (( ${#motivos[@]} == 0 )); then
        echo "RESULTADO: PARÓ en $ms_parada ms"
        if (( ms_parada < 1000 )); then echo "  Cumple el objetivo de la hoja (< 1000 ms)."; else echo "  NO cumple el objetivo de la hoja (< 1000 ms)."; fi
        return 0
    else
        echo "RESULTADO: NO PARÓ"
    fi
    for m in "${motivos[@]}"; do echo "  - $m"; done
    [[ "$rc_parada" == 0 ]] || echo "USA EL CORTE DE ENERGÍA SI EL ROBOT SIGUE MOVIÉNDOSE."
    return 1
}

dir_registro="$HOME/robot_pruebas"
mkdir -p "$dir_registro" || { error "no se pudo crear $dir_registro"; exit 1; }
registro="$dir_registro/prueba-parada_$(date +%F_%H%M%S).log"
principal 2>&1 | tee -a "$registro"
codigo=${PIPESTATUS[0]}
echo "Registro: $registro"
exit "$codigo"
