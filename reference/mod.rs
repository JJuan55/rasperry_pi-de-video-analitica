//! cmd/cva_gestures — Submódulo de backend para Control de Video Analítica (CVA)
//!
//! Expone la gestión de sesiones de gestos, verificación de salud del bridge (8766)
//! y túneles SSH para la Raspberry Pi 5.

pub mod bridge;
pub mod session;

pub use bridge::*;
pub use session::*;
