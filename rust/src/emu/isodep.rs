//! ISO 14443-4 (ISO-DEP) block layer for card emulation: the PICC side of RATS/ATS, I-blocks,
//! R-blocks, S(DESELECT) and chaining of received commands. The application on top sees whole
//! APDUs.
//!
//! Limits: no CID, no NAD, 106 kbit/s only, replies are never chained (an application must keep its
//! response within `MAX_RESPONSE` bytes).
use super::{Target, TargetReply};

/// Fits one I-block of a 256-byte frame.
pub const MAX_RESPONSE: usize = 250;
/// Chained commands are collected up to this size.
pub const MAX_COMMAND: usize = 1024;

/// An application that answers whole APDUs.
pub trait ApduApp {
    /// Fills `resp` (room for `MAX_RESPONSE` bytes) and returns the response length including SW1 SW2.
    fn apdu(&mut self, cmd: &[u8], resp: &mut [u8]) -> usize;
    /// A new ISO-DEP session starts (RATS).
    fn reset(&mut self) {}
    /// After `apdu`: how many S(WTX) "wait longer" requests to send before the answer, for a command
    /// that takes a while (a screen refresh). 0 = answer at once. Read once per command.
    fn take_wtx_requests(&mut self) -> u8 {
        0
    }
}

/// The ATS sent in answer to RATS (TL T0 TA1 TB1 TC1): FSC 256, 106 kbit/s only, FWT about 77 ms.
pub const ATS: [u8; 5] = [0x05, 0x78, 0x80, 0x80, 0x00];

pub struct IsoDepTarget<A: ApduApp> {
    pub app: A,
    activated: bool,
    cmd: [u8; MAX_COMMAND],
    cmd_len: usize,
    last: [u8; MAX_RESPONSE + 3], // last I-block sent, for retransmission after an R(NAK)
    last_len: usize,
    last_bn: u8,
    // a delayed answer: S(WTX) requests go out first, the I-block follows the last WTX response
    pending: [u8; MAX_RESPONSE + 3],
    pending_len: usize,
    wtx_total: u8,
    wtx_sent: u8,
}

impl<A: ApduApp> IsoDepTarget<A> {
    pub fn new(app: A) -> Self {
        Self {
            app,
            activated: false,
            cmd: [0; MAX_COMMAND],
            cmd_len: 0,
            last: [0; MAX_RESPONSE + 3],
            last_len: 0,
            last_bn: 0,
            pending: [0; MAX_RESPONSE + 3],
            pending_len: 0,
            wtx_total: 0,
            wtx_sent: 0,
        }
    }

    pub fn activated(&self) -> bool {
        self.activated
    }
}

impl<A: ApduApp> Target for IsoDepTarget<A> {
    fn reset(&mut self) {
        self.activated = false;
        self.cmd_len = 0;
        self.last_len = 0;
        self.last_bn = 0;
        self.pending_len = 0;
        self.wtx_total = 0;
        self.wtx_sent = 0;
        self.app.reset();
    }

    fn handle(&mut self, f: &[u8], out: &mut [u8]) -> TargetReply {
        let mut r = TargetReply::default();
        if f.is_empty() || out.len() < MAX_RESPONSE + 3 {
            return r;
        }

        if f[0] == 0xE0 {
            // RATS: E0 <FSDI|CID>
            if f.len() != 2 {
                return r;
            }
            self.reset();
            self.activated = true;
            out[..ATS.len()].copy_from_slice(&ATS);
            r.len = ATS.len();
            return r;
        }
        if !self.activated {
            return r;
        }

        let pcb = f[0];
        let bn = pcb & 0x01;

        if pcb & 0xC2 == 0x02 {
            // I-block (bit 2 is always 1)
            if pcb & 0x0C != 0 {
                return r; // CID or NAD present: not supported, stay silent
            }
            let info = &f[1..];
            if self.cmd_len + info.len() > MAX_COMMAND {
                self.cmd_len = 0;
                return r;
            }
            self.cmd[self.cmd_len..self.cmd_len + info.len()].copy_from_slice(info);
            self.cmd_len += info.len();
            if pcb & 0x10 != 0 {
                // chaining: ask for the next block
                out[0] = 0xA2 | bn;
                r.len = 1;
                return r;
            }
            let mut rl = self.app.apdu(&self.cmd[..self.cmd_len], &mut out[1..1 + MAX_RESPONSE]);
            self.cmd_len = 0;
            if rl > MAX_RESPONSE {
                rl = 0;
            }
            out[0] = 0x02 | bn;
            r.len = rl + 1;
            self.last[..r.len].copy_from_slice(&out[..r.len]);
            self.last_len = r.len;
            self.last_bn = bn;
            let wtx = self.app.take_wtx_requests();
            if wtx > 0 {
                // answer later: ask the reader to wait, send the I-block after its last S(WTX) response
                self.pending[..r.len].copy_from_slice(&out[..r.len]);
                self.pending_len = r.len;
                self.wtx_total = wtx;
                self.wtx_sent = 1;
                out[0] = 0xF2; // S(WTX) request, WTXM = 1
                out[1] = 0x01;
                r.len = 2;
            }
            return r;
        }

        if pcb & 0xC2 == 0x82 {
            // R-block: ACK (0xA2/0xA3) or NAK (0xB2/0xB3)
            if pcb & 0x10 != 0 {
                // NAK: repeat the last I-block, or acknowledge if we have none
                if self.last_len != 0 && bn == self.last_bn {
                    out[..self.last_len].copy_from_slice(&self.last[..self.last_len]);
                    r.len = self.last_len;
                } else {
                    out[0] = 0xA2 | bn;
                    r.len = 1;
                }
            } else if self.last_len != 0 && bn == self.last_bn {
                // ACK for a block we already sent: repeat it
                out[..self.last_len].copy_from_slice(&self.last[..self.last_len]);
                r.len = self.last_len;
            }
            return r;
        }

        if pcb & 0xF7 == 0xF2 && self.pending_len != 0 {
            // S(WTX) response from the reader
            if self.wtx_sent < self.wtx_total {
                self.wtx_sent += 1;
                out[0] = 0xF2;
                out[1] = 0x01;
                r.len = 2;
            } else {
                // the wait is over: the delayed answer
                out[..self.pending_len].copy_from_slice(&self.pending[..self.pending_len]);
                r.len = self.pending_len;
                self.pending_len = 0;
            }
            return r;
        }

        if pcb & 0xC7 == 0xC2 && pcb & 0x30 == 0x00 {
            // DESELECT: answer and leave the session
            out[0] = 0xC2;
            r.len = 1;
            r.halt = true;
            self.activated = false;
            self.cmd_len = 0;
        }
        r
    }
}
