# cva_gesture_bridge

Servicio en Python que corre en la Raspberry Pi 5 del laboratorio, dentro del módulo
CVA (Control de Video Analítica) de LaboRemoto. Recibe video del cliente por un túnel
SSH, reconoce gestos de mano (YOLO + OpenCV) y traduce eso en instrucciones para el
hardware del laboratorio (robot Eve3 o domótica con Arduino).

**Antes de tocar cualquier código, lee `CLAUDE.md`** — tiene el contexto completo, el
alcance de la fase actual, y el protocolo de comunicación exacto con el cliente.

`reference/` contiene una copia de solo lectura del código Rust del cliente y de los
archivos de configuración reales, para verificar el protocolo contra la fuente de
verdad en vez de asumir. No se edita desde aquí — el original vive en el repo del
cliente (LaboRemoto/Cliente-Rust).
