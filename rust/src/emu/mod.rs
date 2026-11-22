//! Tag emulation models: what a card answers to one command frame. No hardware access, so
//! everything here is host-tested. Ported from `../arduino/src` (Target.h, Type2Tag, IsoDepTarget,
//! ApduApps); the chip-side state machine is `listener`.

pub mod apps;
pub mod isodep;
pub mod type2tag;

/// What to send back for one frame.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct TargetReply {
    /// Reply bytes (the chip adds the CRC); 0 with `ack4 == false`: no reply.
    pub len: usize,
    /// Send the 4-bit frame `nibble` instead (Type 2: ACK = 0xA, NAK = 0x0).
    pub ack4: bool,
    pub nibble: u8,
    /// Go to the halt state after sending the reply (HLTA, DESELECT).
    pub halt: bool,
}

/// A tag or card model driven by the listener.
pub trait Target {
    /// One frame from the reader (CRC removed). `out` has room for at least 260 bytes.
    fn handle(&mut self, frame: &[u8], out: &mut [u8]) -> TargetReply;
    /// A new session starts (the reader selected us again): forget any protocol state.
    fn reset(&mut self) {}
}

#[cfg(test)]
mod tests;
