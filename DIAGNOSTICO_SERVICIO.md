# DIAGNOSTICO_SERVICIO.md — Tarea operativa para Claude Code en Pi5

**No es una fase nueva de `BITACORA.md`.** Esto es limpieza operativa del servicio
`systemd` que corre `cva_gesture_bridge` — no toca ni una línea de
`cva_gesture_bridge/*.py`. Si en algún punto de este diagnóstico parece que hace falta
cambiar código de la aplicación (no de configuración de servicio), detente y pregunta
antes de tocarlo — no es el alcance de esta tarea.

## Contexto — qué se observó

Al arrancar `cva-gesture-bridge.service` con `systemd`, el log mostró esto:

```
OSError: [Errno 98] error while attempting to bind on address ('0.0.0.0', 8766): address already in use
... (unos segundos después) ...
cva_gesture_bridge escuchando en ('0.0.0.0', 8766)   [PID A]
... (5 segundos después, sin error visible entre medio) ...
cva_gesture_bridge escuchando en ('0.0.0.0', 8766)   [PID B, distinto de A]
```

Dos síntomas a investigar:
1. **El "address already in use" inicial** sugiere que algo más ya tenía el puerto 8766
   ocupado al momento de arrancar el servicio — sospecha principal: el proceso manual
   (`nohup .venv/bin/python -m cva_gesture_bridge.main`, o dentro de una sesión `tmux`)
   que se dejó corriendo durante la sesión de Fase 6/7, antes de que existiera el
   servicio `systemd`.
2. **Un segundo "escuchando" 5 segundos después del primero, con otro PID**, sin ningún
   error visible en el medio — sugiere que el proceso que sí logró bindear el puerto
   salió y `systemd` lo reinició (`Restart=always`), pero no está claro por qué salió.

## Qué hacer, en este orden exacto

### 1. Inventario de todo lo que pueda estar escuchando en el 8766 o corriendo el bridge

```bash
echo "--- procesos cva_gesture_bridge ---"
ps aux | grep -i cva_gesture_bridge | grep -v grep

echo "--- sesiones tmux activas ---"
tmux ls 2>&1

echo "--- quién tiene el puerto 8766 ---"
sudo ss -ltnp | grep 8766

echo "--- estado del servicio systemd ---"
sudo systemctl status cva-gesture-bridge.service --no-pager -l
```

Reporta la salida completa de las cuatro antes de tocar nada.

### 2. Si aparece más de un proceso de `cva_gesture_bridge` (o una sesión `tmux` vieja con el bridge adentro)

El criterio para decidir cuál matar: **el proceso administrado por `systemd` es el que
se queda** (es el único con reinicio automático y logueo centralizado vía
`journalctl`). Cualquier otro (`nohup` suelto, `tmux` de la sesión de Fase 6/7) se
termina:

```bash
# identifica el PID del proceso viejo (el que NO es hijo de systemd) con el ss/ps de arriba
kill <PID_DEL_PROCESO_VIEJO>

# si estaba dentro de una sesión tmux, se puede matar la sesión entera:
tmux kill-session -t cva-bridge   # o el nombre real que tenga
```

**No mates el proceso que aparece bajo `systemctl status` como el `MainPID` actual del
servicio** — ese es el que debe quedar vivo.

### 3. Confirmar que el servicio queda estable después de la limpieza

```bash
sudo systemctl restart cva-gesture-bridge.service
sleep 5
sudo systemctl status cva-gesture-bridge.service --no-pager -l
```

Busca específicamente en la salida:
- `Active: active (running)` — no `activating` ni `failed`.
- El campo de reinicios (systemd lo muestra distinto según versión, puede ser
  necesario `systemctl show cva-gesture-bridge.service -p NRestarts`) — debería
  quedarse en 0 o 1 después de este restart manual, no seguir subiendo solo.

Si sigue reiniciándose sin que nadie lo toque, no lo dejes así — reporta el log
completo de `journalctl -u cva-gesture-bridge.service --since "2 min ago" --no-pager`
en vez de asumir que ya quedó bien.

### 4. Confirmar que después de la limpieza el binding es único y limpio

```bash
sudo ss -ltnp | grep 8766
```

Debe aparecer **una sola línea**, con el PID que coincide con el `MainPID` reportado
por `systemctl status`.

### 5. Re-verificar el health check local (equivalente al Paso C de la guía del forward)

```bash
nc -zv 127.0.0.1 8766
journalctl -u cva-gesture-bridge.service -n 5 --no-pager
```

Debe verse la línea de "cerrada sin datos (health check)" correspondiente a esta prueba.

## Al terminar

Registra en `BITACORA.md` una nota breve, bajo un encabezado tipo
`### Nota operativa — limpieza de proceso duplicado (fecha)`, **no** como parte de la
sección de Fase 7 (esto no es desarrollo, es operación del servicio). Incluye:
- Qué proceso(s) duplicado(s) se encontró y de dónde venían (nohup/tmux viejo).
- Confirmación de que `cva-gesture-bridge.service` quedó estable (salida real de
  `systemctl status` después de la limpieza).
- Confirmación del health check limpio del paso 5.

Reporta también directamente a JD (o pégalo para que Claude, en el chat, lo revise)
con la salida literal de los comandos — no un resumen. Después de esto, JD puede
repetir la prueba completa con el cliente real (cámara + práctica) para el checkpoint
de Fase 7, ahora sobre un servicio confirmado estable.
