//! An NTAG215-like NFC Forum Type 2 tag in RAM.
//!
//! * `READ 30 pp`: 16 bytes (wraps past the last page, as a real tag does)
//! * `FAST_READ 3A s e`: pages s..e
//! * `WRITE A2 pp d0..3`: ACK; pages 0-2 (UID, lock) are refused with a NAK
//! * `GET_VERSION 60`: NTAG215 `00 04 04 02 01 00 11 03`
//! * `READ_SIG 3C 00`: 32 zero bytes (an emulated tag has no signature)
//! * `HLTA 50 00`: no reply, the tag goes to the halt state
//! * anything else: NAK
use super::{Target, TargetReply};

pub const PAGES: usize = 135;
pub const BYTES: usize = PAGES * 4;

pub struct Type2Tag {
    mem: [u8; BYTES],
    changed: [u8; (PAGES + 7) / 8],
    writes: usize,
}

fn nak(code: u8) -> TargetReply {
    TargetReply { ack4: true, nibble: code, ..TargetReply::default() }
}

impl Type2Tag {
    /// 7-byte UID (BCC bytes are computed), capability container `E1 10 3E 00`, empty NDEF message.
    pub fn new(uid: [u8; 7]) -> Self {
        let mut mem = [0u8; BYTES];
        mem[0..3].copy_from_slice(&uid[0..3]);
        mem[3] = 0x88 ^ uid[0] ^ uid[1] ^ uid[2]; // BCC0 (cascade tag 0x88)
        mem[4..8].copy_from_slice(&uid[3..7]);
        mem[8] = uid[3] ^ uid[4] ^ uid[5] ^ uid[6]; // BCC1
        mem[12..16].copy_from_slice(&[0xE1, 0x10, 0x3E, 0x00]);
        // Configuration pages of a blank NTAG215: no protection (AUTH0 = FF), default password.
        mem[130 * 4 + 3] = 0xBD; // dynamic lock bytes
        mem[131 * 4 + 3] = 0xFF; // AUTH0: no page needs the password
        mem[132 * 4 + 1] = 0x05; // ACCESS / VCTID
        mem[133 * 4..133 * 4 + 4].copy_from_slice(&[0xFF; 4]); // PWD
        mem[16..19].copy_from_slice(&[0x03, 0x00, 0xFE]); // empty NDEF TLV
        Self { mem, changed: [0; (PAGES + 7) / 8], writes: 0 }
    }

    pub fn memory(&self) -> &[u8; BYTES] {
        &self.mem
    }

    pub fn write_count(&self) -> usize {
        self.writes
    }

    /// True when a WRITE changed the page since construction or `clear_changed`.
    pub fn changed(&self, page: usize) -> bool {
        page < PAGES && (self.changed[page >> 3] >> (page & 7)) & 1 == 1
    }

    pub fn clear_changed(&mut self) {
        self.changed = [0; (PAGES + 7) / 8];
    }
}

impl Target for Type2Tag {
    fn handle(&mut self, f: &[u8], out: &mut [u8]) -> TargetReply {
        let mut r = TargetReply::default();
        if f.is_empty() || out.len() < 64 {
            return r;
        }
        match f[0] {
            0x30 => {
                // READ
                if f.len() != 2 || f[1] as usize >= PAGES {
                    return nak(0x0);
                }
                for i in 0..16 {
                    out[i] = self.mem[(f[1] as usize * 4 + i) % BYTES];
                }
                r.len = 16;
            }
            0x3A => {
                // FAST_READ
                if f.len() != 3 || f[1] > f[2] || f[2] as usize >= PAGES {
                    return nak(0x0);
                }
                let bytes = (f[2] as usize - f[1] as usize + 1) * 4;
                if bytes > out.len() {
                    return nak(0x0);
                }
                out[..bytes].copy_from_slice(&self.mem[f[1] as usize * 4..f[1] as usize * 4 + bytes]);
                r.len = bytes;
            }
            0xA2 => {
                // WRITE
                if f.len() != 6 {
                    return nak(0x1);
                }
                let p = f[1] as usize;
                if p < 3 || p >= PAGES {
                    return nak(0x0);
                }
                if self.mem[p * 4..p * 4 + 4] != f[2..6] {
                    self.mem[p * 4..p * 4 + 4].copy_from_slice(&f[2..6]);
                    self.changed[p >> 3] |= 1 << (p & 7);
                }
                self.writes += 1;
                r.ack4 = true;
                r.nibble = 0xA;
            }
            0x60 => {
                // GET_VERSION (NTAG215)
                out[..8].copy_from_slice(&[0x00, 0x04, 0x04, 0x02, 0x01, 0x00, 0x11, 0x03]);
                r.len = 8;
            }
            0x3C => {
                // READ_SIG
                out[..32].fill(0);
                r.len = 32;
            }
            0x50 => {
                // HLTA
                if f.len() == 2 && f[1] == 0x00 {
                    r.halt = true;
                } else {
                    return nak(0x0);
                }
            }
            _ => return nak(0x0),
        }
        r
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const UID: [u8; 7] = [0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66];

    fn h(tag: &mut Type2Tag, f: &[u8], out: &mut [u8; 64]) -> TargetReply {
        tag.handle(f, out)
    }

    #[test]
    fn type2_tag() {
        let mut tag = Type2Tag::new(UID);
        let mut out = [0u8; 64];

        // READ page 0: UID with BCC bytes
        let r = h(&mut tag, &[0x30, 0x00], &mut out);
        assert!(r.len == 16 && !r.ack4);
        assert_eq!(out[0], 0x04);
        assert_eq!(out[3], 0x88 ^ 0x04 ^ 0x11 ^ 0x22);
        assert_eq!(out[4], 0x33);
        assert_eq!(out[8], 0x33 ^ 0x44 ^ 0x55 ^ 0x66);
        assert_eq!(&out[12..15], &[0xE1, 0x10, 0x3E]);

        // a blank NTAG215: AUTH0 = FF (no password protection), PWD = FF FF FF FF
        let r = h(&mut tag, &[0x30, 0x83], &mut out);
        assert!(r.len == 16 && out[3] == 0xFF && out[8] == 0xFF && out[11] == 0xFF);

        // READ wraps past the last page
        let r = h(&mut tag, &[0x30, 0x86], &mut out);
        let mem = *tag.memory();
        assert!(r.len == 16 && out[4..16] == mem[..12]);
        // READ out of range
        let r = h(&mut tag, &[0x30, 0x87], &mut out);
        assert!(r.len == 0 && r.ack4 && r.nibble == 0x0);

        // FAST_READ pages 3..5
        let r = h(&mut tag, &[0x3A, 0x03, 0x05], &mut out);
        assert!(r.len == 12 && out[0] == 0xE1);
        let r = h(&mut tag, &[0x3A, 0x05, 0x03], &mut out);
        assert!(r.ack4 && r.nibble == 0x0);

        // WRITE: ACK, stored, marked changed
        assert!(!tag.changed(4));
        let r = h(&mut tag, &[0xA2, 0x04, 0x03, 0x0A, 0xD1, 0x01], &mut out);
        assert!(r.ack4 && r.nibble == 0xA && tag.write_count() == 1);
        assert!(tag.memory()[16] == 0x03 && tag.memory()[19] == 0x01);
        assert!(tag.changed(4) && !tag.changed(5));
        // writing the same data again is still ACKed but does not mark the page again
        tag.clear_changed();
        let r = h(&mut tag, &[0xA2, 0x04, 0x03, 0x0A, 0xD1, 0x01], &mut out);
        assert!(r.ack4 && r.nibble == 0xA && !tag.changed(4));
        // UID and lock pages are refused
        let r = h(&mut tag, &[0xA2, 0x00, 1, 2, 3, 4], &mut out);
        assert!(r.ack4 && r.nibble == 0x0 && tag.memory()[0] == 0x04);
        let r = h(&mut tag, &[0xA2, 0x02, 1, 2, 3, 4], &mut out);
        assert!(r.ack4 && r.nibble == 0x0);
        // wrong length
        let r = h(&mut tag, &[0xA2, 0x04, 1, 2, 3], &mut out);
        assert!(r.ack4 && r.nibble == 0x1);

        // GET_VERSION, READ_SIG
        let r = h(&mut tag, &[0x60], &mut out);
        assert!(r.len == 8 && out[2] == 0x04 && out[6] == 0x11);
        let r = h(&mut tag, &[0x3C, 0x00], &mut out);
        assert_eq!(r.len, 32);

        // HLTA ends the session without a reply
        let r = h(&mut tag, &[0x50, 0x00], &mut out);
        assert!(r.halt && r.len == 0 && !r.ack4);

        // unknown command: NAK
        let r = h(&mut tag, &[0x99], &mut out);
        assert!(r.ack4 && r.nibble == 0x0);
    }
}
