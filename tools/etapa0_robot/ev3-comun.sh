# Funciones comunes de las herramientas de la Etapa 0 del robot (se carga con `source`).
#
# Solo bash, curl y python3 (biblioteca estándar). No usa nada de cva_gesture_bridge/:
# si la cadena de gestos falla, estas herramientas siguen funcionando.
# Rutas usadas, todas de docs/CONTRATO_ROBOT_EV3.md: GET /status, GET /estado_ejecucion,
# POST /ejecutar_ruta, POST /detener_ejecucion.
#
# Nunca imprime la URL del robot (ni en pantalla ni en registros) y curl va en modo
# silencioso: sus mensajes de error incluyen la dirección. Los fallos se traducen aquí.

PREGUNTA_SEGURIDAD="¿Robot elevado o con espacio libre y laboratorista presente con el corte de energía a mano? Escribe SI"
GRADOS_MIN=1
GRADOS_MAX=180
POTENCIA_MIN=1
POTENCIA_MAX=30

error() { echo "ERROR: $*" >&2; }

# Milisegundos desde la época (reloj de pared). Con $EPOCHREALTIME de bash (5.0 o
# posterior); si no está, con python3. No se usa `date +%3N`: no todas las versiones de
# date lo respetan.
ahora_ms() {
    if [[ -n "${EPOCHREALTIME:-}" ]]; then
        local us=${EPOCHREALTIME/[.,]/}
        echo $(( 10#$us / 1000 ))
    else
        python3 -c 'import time; print(int(time.time() * 1000))'
    fi
}

# Hora local HH:MM:SS.mmm
hora() {
    local ms
    ms=$(ahora_ms)
    printf '%(%H:%M:%S)T.%03d' $(( ms / 1000 )) $(( ms % 1000 ))
}

# Duerme hasta el instante MS (ahora_ms); si ya pasó, vuelve enseguida.
dormir_hasta() {
    local d=$(( $1 - $(ahora_ms) ))
    (( d > 0 )) && sleep "$(printf '%d.%03d' $(( d / 1000 )) $(( d % 1000 )))"
    return 0
}

# Entero estricto dentro de [min, max]. Fuera de rango es un error: no se recorta.
validar_entero() {  # nombre valor min max
    local nombre=$1 v=$2 min=$3 max=$4
    if [[ ! "$v" =~ ^[0-9]+$ ]] || (( 10#$v < min || 10#$v > max )); then
        error "$nombre=$v fuera de rango: debe ser un entero entre $min y $max. No se hizo nada."
        return 1
    fi
}

# Dirección del robot, sin valor por defecto (la IP cambia por DHCP). Orden: --url,
# variable EV3_URL, archivo ~/.ev3_url (una línea). Deja ROBOT_URL y URL_ORIGEN.
resolver_url() {  # $1 = valor de --url (puede ir vacío)
    local u="" origen=""
    if [[ -n "${1:-}" ]]; then
        u=$1; origen="--url"
    elif [[ -n "${EV3_URL:-}" ]]; then
        u=$EV3_URL; origen="EV3_URL"
    elif [[ -r "$HOME/.ev3_url" ]]; then
        IFS= read -r u < "$HOME/.ev3_url" || true
        u=${u//[[:space:]]/}; origen="~/.ev3_url"
    fi
    if [[ -z "$u" ]]; then
        error "no hay dirección del robot. Usa --url http://HOST:PUERTO, la variable EV3_URL o el archivo ~/.ev3_url (una línea). No se hizo nada."
        return 1
    fi
    u=${u%/}
    if [[ ! "$u" =~ ^https?://[^[:space:]/]+$ ]]; then
        error "la dirección tomada de $origen no es válida: debe ser http://HOST:PUERTO, sin ruta. No se hizo nada."
        return 1
    fi
    ROBOT_URL=$u
    URL_ORIGEN=$origen
}

fallo_curl() {
    case $1 in
        6) echo "no se pudo resolver el nombre del robot" ;;
        7) echo "no se pudo conectar con el robot" ;;
        28) echo "sin respuesta en ${MAX_TIME:-2} s (tiempo agotado)" ;;
        52) echo "el robot cerró la conexión sin responder" ;;
        55 | 56) echo "la conexión se cortó durante la petición" ;;
        *) echo "curl falló (código $1)" ;;
    esac
}

# peticion MÉTODO RUTA [CUERPO_JSON]
# Deja RESP_CODIGO (000 si no hubo respuesta), RESP_CUERPO y RESP_FALLO (vacío si hubo
# respuesta). Tiempo máximo: MAX_TIME (2 s si no se fija). Un POST sin cuerpo va con
# Content-Length: 0 y sin Content-Type, como lo manda el cliente del bridge.
peticion() {
    local metodo=$1 ruta=$2 cuerpo=${3:-} tmp rc
    tmp=$(mktemp)
    local args=(-s --noproxy '*' --max-time "${MAX_TIME:-2}" -o "$tmp" -w '%{http_code}' -X "$metodo")
    if [[ -n "$cuerpo" ]]; then
        args+=(-H 'Content-Type: application/json' --data-binary "$cuerpo")
    elif [[ "$metodo" == POST ]]; then
        args+=(-H 'Content-Type:' --data-binary '')
    fi
    RESP_CODIGO=$(curl "${args[@]}" "$ROBOT_URL$ruta")
    rc=$?
    RESP_CUERPO=$(cat "$tmp")
    rm -f "$tmp"
    RESP_FALLO=""
    if (( rc != 0 )); then
        RESP_FALLO=$(fallo_curl "$rc")
        RESP_CODIGO=000
    fi
}

# json_campos CUERPO CAMPO... -> una línea por campo, en JSON (true, false, null, 1,
# "texto") o AUSENTE. Falla si el cuerpo no es un objeto JSON. Sin conversiones: 1.0 no
# es 1 y "true" no es true.
json_campos() {
    python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1])
except ValueError:
    sys.exit(1)
if not isinstance(d, dict):
    sys.exit(1)
for k in sys.argv[2:]:
    print(json.dumps(d[k], ensure_ascii=False) if k in d else "AUSENTE")
' "$@"
}

# GET /status. Deja STATUS_BATERIA y, para outC y outD, *_CONECTADO (si/no/falta),
# *_VEL y *_POS (? si no viene un número). Devuelve 1 si no hay respuesta válida.
leer_status() {
    local salida
    peticion GET /status
    if [[ "$RESP_CODIGO" != 200 ]]; then
        RESP_FALLO=${RESP_FALLO:-"HTTP $RESP_CODIGO"}
        return 1
    fi
    salida=$(python3 -c '
import json, sys
try:
    d = json.loads(sys.argv[1])
except ValueError:
    sys.exit(1)
if not isinstance(d, dict):
    sys.exit(1)
num = lambda v: str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else "?"
print(num(d.get("battery")))
motores = {m.get("port"): m for m in d.get("motors") or [] if isinstance(m, dict)}
for p in ("outC", "outD"):
    m = motores.get(p)
    if m is None:
        print("falta ? ?")
    else:
        print("si" if m.get("connected") is True else "no", num(m.get("speed")), num(m.get("position")))
' "$RESP_CUERPO") || { RESP_FALLO="respuesta de /status no es JSON válido"; return 1; }
    {
        read -r STATUS_BATERIA
        read -r C_CONECTADO C_VEL C_POS
        read -r D_CONECTADO D_VEL D_POS
    } <<< "$salida"
}

mostrar_motores() {  # etiqueta
    echo "[$(hora)] $1 outC (rueda izq.): velocidad=$C_VEL posición=$C_POS | outD (rueda der.): velocidad=$D_VEL posición=$D_POS"
}

pedir_confirmacion() {
    local r=""
    echo "$PREGUNTA_SEGURIDAD"
    IFS= read -r r || true
    if [[ "$r" != "SI" ]]; then
        echo "No se escribió SI: no se envía nada."
        return 1
    fi
    echo "Confirmación recibida (SI)."
}

# GET /estado_ejecucion: 0 solo si responde ejecutando:false.
comprobar_libre() {
    local campos ej
    peticion GET /estado_ejecucion
    if [[ "$RESP_CODIGO" != 200 ]]; then
        error "GET /estado_ejecucion: ${RESP_FALLO:-HTTP $RESP_CODIGO}. No se sabe si hay una ruta en curso: no se envía."
        return 1
    fi
    campos=$(json_campos "$RESP_CUERPO" ejecutando pasoActual totalPasos) || {
        error "GET /estado_ejecucion no devolvió JSON válido: no se envía."
        return 1
    }
    { read -r ej; read -r ESTADO_PASO; read -r ESTADO_TOTAL; } <<< "$campos"
    case $ej in
        false) echo "[$(hora)] Estado de ejecución: libre (ejecutando:false)." ;;
        true) error "el robot ya está ejecutando una ruta (paso $ESTADO_PASO de $ESTADO_TOTAL): no se envía."; return 1 ;;
        *) error "GET /estado_ejecucion: campo ejecutando=$ej inesperado: no se envía."; return 1 ;;
    esac
}

# /status (batería y motores) y /estado_ejecucion antes de mover.
comprobar_antes_de_mover() {
    if ! leer_status; then
        error "GET /status: $RESP_FALLO. No se envía nada."
        return 1
    fi
    echo "[$(hora)] Batería: $STATUS_BATERIA V"
    echo "[$(hora)] outC (rueda izq.): conectado=$C_CONECTADO | outD (rueda der.): conectado=$D_CONECTADO"
    if [[ "$C_CONECTADO" != si || "$D_CONECTADO" != si ]]; then
        error "outC y outD deben aparecer conectados en /status (el robot ignora SIN AVISAR un motor no conectado). No se envía nada."
        return 1
    fi
    comprobar_libre
}

# Mecanismo de parada (ev3-parar.sh y ev3-prueba-parada.sh usan este mismo):
# POST /detener_ejecucion hasta 3 veces, con 0,2 s entre intentos, hasta recibir ok:true;
# luego GET /estado_ejecucion hasta ver ejecutando:false dos veces seguidas, como mucho
# 10 s desde el primer POST. Cada petición, 2 s como máximo (menos si queda menos plazo).
# Sin confirmación previa y sin /status. Deja PARADA_MS (del primer POST a la
# confirmación) y devuelve 0 solo si la parada quedó confirmada.
parar_y_confirmar() {
    local t0 i ok_detener=no seguidos=0 limite restante ej msj ultimo="" consultas=0
    PARADA_MS=""
    t0=$(ahora_ms)
    limite=$(( t0 + 10000 ))
    echo "[$(hora)] POST /detener_ejecucion"
    for i in 1 2 3; do
        MAX_TIME=2 peticion POST /detener_ejecucion
        if [[ "$RESP_CODIGO" == 200 ]] && [[ "$(json_campos "$RESP_CUERPO" ok 2>/dev/null)" == true ]]; then
            echo "[$(hora)] /detener_ejecucion intento $i: ok:true"
            ok_detener=si
            break
        fi
        echo "[$(hora)] /detener_ejecucion intento $i: ${RESP_FALLO:-HTTP $RESP_CODIGO, sin ok:true}"
        (( i < 3 )) && sleep 0.2
    done
    while :; do
        restante=$(( limite - $(ahora_ms) ))
        (( restante <= 0 )) && break
        (( restante > 2000 )) && restante=2000
        MAX_TIME=$(printf '%d.%03d' $(( restante / 1000 )) $(( restante % 1000 ))) peticion GET /estado_ejecucion
        consultas=$(( consultas + 1 ))
        # Aquí no se lanza python3 por cada consulta (~50 ms en una Pi): la medida en ms
        # incluiría ese tiempo. Se busca el campo booleano en el cuerpo.
        ej=""
        if [[ "$RESP_CODIGO" == 200 && "$RESP_CUERPO" =~ \"ejecutando\"[[:space:]]*:[[:space:]]*(true|false) ]]; then
            ej=${BASH_REMATCH[1]}
        fi
        if [[ "$ej" == false ]]; then
            seguidos=$(( seguidos + 1 ))
            echo "[$(hora)] /estado_ejecucion: ejecutando:false ($seguidos de 2)"
            ultimo=""
            if (( seguidos >= 2 )); then
                PARADA_MS=$(( $(ahora_ms) - t0 ))
                echo "[$(hora)] PARADA CONFIRMADA en $PARADA_MS ms (desde el primer POST /detener_ejecucion)"
                if [[ "$ok_detener" == no ]]; then
                    echo "AVISO: /detener_ejecucion nunca respondió ok:true. El robot no está ejecutando, pero no hay constancia de que recibiera la orden de parada."
                fi
                return 0
            fi
        else
            # Solo los cambios: una línea por consulta taparía el aviso final.
            seguidos=0
            msj="${RESP_FALLO:-ejecutando=${ej:-respuesta no válida}}"
            [[ "$msj" != "$ultimo" ]] && echo "[$(hora)] /estado_ejecucion: $msj (se sigue consultando)"
            ultimo=$msj
        fi
        sleep 0.1
    done
    echo "[$(hora)] PARADA NO CONFIRMADA EN 10 S ($consultas consultas). USA EL CORTE DE ENERGÍA SI EL ROBOT SIGUE MOVIÉNDOSE."
    return 1
}
