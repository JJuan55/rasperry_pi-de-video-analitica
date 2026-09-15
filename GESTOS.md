# GESTOS.md — Propuesta de catálogo de gestos (cva_gesture_bridge)

**Estado: APROBADO por JD el 2026-09-10, con un ajuste — sujeto a revisión cuando se
definan los controles reales de cada dispositivo (Fase 9).** El catálogo de gestos
crudos y el mapeo tentativo quedan aprobados como punto de partida documentado, no como
decisión final e inamovible — cuando se empiece a definir con hardware real qué
controles necesita cada módulo, este documento se revisa y ajusta. La implementación
real del mapeo gesto→instrucción sigue siendo alcance de Fase 9 (ver `CLAUDE.md`,
sección 7); esto solo fija el punto de partida acordado, tal como pedía `CLAUDE.md`
sección 4.4.

**Ajuste aprobado (revisión de Claude + JD, 2026-09-10):** `dedo_medio` se retira de los
mapeos de **ambos** módulos (no solo Robot) — un gesto técnico de detección de una sola
mano extendida se puede leer como ofensivo en un entorno universitario con otras
personas presentes, y no vale la pena el riesgo cuando hay alternativas (`dedo_pulgar`
cubre "girar derecha" en Robot). Sigue existiendo en el catálogo de gestos crudos que
reconoce la visión — el ajuste es solo que nunca se traduce a una instrucción real en
ningún módulo.

## Por qué hace falta definir esto

Se revisaron los archivos reales de configuración del cliente
(`reference/config/domotica.json` y `reference/config/robot.json`, copias de solo
lectura) y ambos tienen `"mapping": {}` — vacío. El catálogo de gestos no existe
todavía en ningún lado del proyecto; hay que proponerlo y aprobarlo desde cero, no
asumir que ya está decidido.

El único punto de referencia real y ya probado contra el código Rust del cliente
(`reference/session.rs`, test `test_cva_send_frame_success_with_real_listener`) es el
mensaje:

```
gesto: dedo anular, instruccion: mover adelante
```

Esta propuesta se construyó a partir de ese único dato fijo, extendiéndolo a un esquema
completo de "qué dedo(s) están extendidos".

## Catálogo de gestos crudos (independiente del módulo)

Lo que YOLO/OpenCV debería reconocer en el frame, antes de cualquier mapeo a
instrucción:

| Gesto | Descripción |
|---|---|
| `puño_cerrado` | Ningún dedo extendido |
| `palma_abierta` | Los 5 dedos extendidos |
| `dedo_pulgar` | Solo el pulgar extendido |
| `dedo_indice` | Solo el índice extendido |
| `dedo_medio` | Solo el medio extendido |
| `dedo_anular` | Solo el anular extendido (**ya probado y fijo** — ver arriba) |
| `dedo_menique` | Solo el meñique extendido |

## Mapeo tentativo por módulo (solo propuesta, no implementado)

### Robot (Eve3 — movimiento)

| Gesto | Instrucción tentativa |
|---|---|
| `palma_abierta` | avanzar |
| `puño_cerrado` | detener |
| `dedo_indice` | girar izquierda |
| `dedo_anular` | mover adelante (fijo, ya probado) |
| `dedo_menique` | retroceder |

### Domótica (Arduino)

| Gesto | Instrucción tentativa |
|---|---|
| `palma_abierta` | encender luces |
| `puño_cerrado` | apagar luces |
| `dedo_pulgar` | abrir puerta |
| `dedo_indice` | cerrar puerta |
| `dedo_anular` | (sin asignar — a definir) |
| `dedo_menique` | (sin asignar — a definir) |

## Pendiente de resolver antes de que esto sea definitivo

- Confirmar o ajustar el resto del mapeo tentativo de ambos módulos.
- Decidir qué pasa con los gestos "sin asignar" en Domótica — ¿se dejan sin instrucción
  (ignorados) o se completan con más acciones del módulo (ventilador, alarma, etc.)?
- Resolver el hueco de `module_id` (`CLAUDE.md` sección 4.5) — el bridge todavía no sabe
  por ningún canal si la sesión activa es `domotica` o `robot`, así que no puede aplicar
  ninguno de estos mapeos todavía aunque estén aprobados.
- Una vez aprobado, este catálogo debe reflejarse en `config/cva-gestures/domotica.json`
  y `config/cva-gestures/robot.json` del lado del cliente (Fase 9), y en la configuración
  equivalente que el bridge use para su propio mapeo — no antes.
