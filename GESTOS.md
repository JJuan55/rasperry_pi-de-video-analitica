# GESTOS.md — Propuesta de catálogo de gestos (cva_gesture_bridge)

**Estado: PROPUESTA, pendiente de aprobación explícita de JD.** Nada de esto está
implementado todavía — ni el catálogo de gestos crudos, ni el mapeo por módulo. La
implementación real del mapeo gesto→instrucción es alcance de Fase 9 (ver `CLAUDE.md`,
sección 7); este documento solo existe para discutir y cerrar la decisión antes de esa
fase, tal como pide `CLAUDE.md` sección 4.4 ("el formato exacto... se termina de definir
junto con JD en la Fase 7/8").

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
| `dedo_medio` | girar derecha ⚠️ |

⚠️ **Nota abierta:** usar `dedo_medio` aislado como gesto técnico puede leerse como un
gesto ofensivo aunque la intención sea puramente de detección de visión. Alternativa
sugerida: usar `dedo_pulgar` para "girar derecha" y liberar `dedo_medio` sin asignar (o
no incluirlo en el catálogo de Robot). **Pendiente de decisión de JD.**

### Domótica (Arduino)

| Gesto | Instrucción tentativa |
|---|---|
| `palma_abierta` | encender luces |
| `puño_cerrado` | apagar luces |
| `dedo_pulgar` | abrir puerta |
| `dedo_indice` | cerrar puerta |
| `dedo_anular` | (sin asignar — a definir) |
| `dedo_menique` | (sin asignar — a definir) |
| `dedo_medio` | (sin asignar — a definir) |

## Pendiente de resolver antes de que esto sea definitivo

- Confirmar o ajustar el mapeo tentativo de ambos módulos (especialmente el punto del
  `dedo_medio` en Robot).
- Decidir qué pasa con los gestos "sin asignar" en Domótica — ¿se dejan sin instrucción
  (ignorados) o se completan con más acciones del módulo (ventilador, alarma, etc.)?
- Resolver el hueco de `module_id` (`CLAUDE.md` sección 4.5) — el bridge todavía no sabe
  por ningún canal si la sesión activa es `domotica` o `robot`, así que no puede aplicar
  ninguno de estos mapeos todavía aunque estén aprobados.
- Una vez aprobado, este catálogo debe reflejarse en `config/cva-gestures/domotica.json`
  y `config/cva-gestures/robot.json` del lado del cliente (Fase 9), y en la configuración
  equivalente que el bridge use para su propio mapeo — no antes.
