//! cmd/cva_gestures/bridge.rs — Verificación de salud y túnel SSH para la Pi 5 (puerto 8766)

use crate::cmd::protocol::CommandError;
use crate::cmd::state::SESSIONS;
use crate::error::AppError;

pub const DEFAULT_CVA_BRIDGE_PORT: u16 = 8766;

/// Verifica si el bridge de video analítica (puerto 8766 en la Pi 5) está escuchando y disponible.
pub async fn check_bridge_health(session_id: &str, target_port: u16) -> Result<(), CommandError> {
    let handle = {
        let map = SESSIONS
            .lock()
            .map_err(|_| CommandError::internal("LOCK_POISONED", "SESSIONS lock poisoned"))?;
        let sess = map
            .get(session_id)
            .ok_or_else(|| CommandError::from(AppError::NotFoundSession))?;
        sess.term.handle.clone()
    };

    let lock = handle.lock().await;
    match lock
        .channel_open_direct_tcpip("127.0.0.1", target_port as u32, "127.0.0.1", 0)
        .await
    {
        Ok(ch) => {
            let _ = ch.close().await;
            Ok(())
        }
        Err(e) => Err(CommandError::transient(
            "BRIDGE_UNAVAILABLE",
            format!(
                "El servidor de video analítica (puerto {target_port}) no responde en el host remoto: {e}"
            ),
        )),
    }
}
