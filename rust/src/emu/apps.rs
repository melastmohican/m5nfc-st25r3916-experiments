//! Applications for the ISO-DEP target (`isodep`).
//!
//! * `Type4NdefApp`: an NFC Forum Type 4 tag: SELECT the NDEF application (D2760000850101), the
//!   capability container E103 and the NDEF file E104, READ BINARY and UPDATE BINARY.
//! * `SniffApp`: records every APDU in full. Class 00 commands (SELECT, READ BINARY) are served by a
//!   real Type 4 NDEF tag, so the phone's own background tag reader finishes instead of looping;
//!   everything else gets 90 00, so an app that sends a vendor protocol (for example F0 D2 image
//!   packets) keeps talking.
use super::isodep::ApduApp;

/// NLEN (2 bytes) + message.
pub const NDEF_FILE_SIZE: usize = 1024;
/// MLe / MLc announced in the capability container.
pub const MAX_READ: usize = 0xF0;
pub const CAPTURE_SIZE: usize = 16384;
/// After a screen refresh (F0 D4) the first `BUSY_POLLS` busy queries (F0 DE) answer "busy" (01), like
/// a real panel that needs a second or two; then "idle" (00).
pub const BUSY_POLLS: u8 = 3;
/// A real e-paper board answers the refresh command (F0 D4) with S(WTX) "wait longer" requests, five
/// of them in the Flipper Zero app's model of a Good Display board, then 90 00.
pub const REFRESH_WTX_REQUESTS: u8 = 5;

const NDEF_AID: [u8; 7] = [0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01];

fn sw(resp: &mut [u8], sw1: u8, sw2: u8) -> usize {
    resp[0] = sw1;
    resp[1] = sw2;
    2
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Selected {
    None,
    App,
    Cc,
    NdefFile,
}

pub struct Type4NdefApp {
    cc: [u8; 15],
    ndef: [u8; NDEF_FILE_SIZE],
    selected: Selected,
    app_selected: bool,
    updates: usize,
}

impl Type4NdefApp {
    pub fn new() -> Self {
        let cc = [
            0x00,
            0x0F, // CCLEN
            0x20, // mapping version 2.0
            0x00,
            MAX_READ as u8,
            0x00,
            MAX_READ as u8, // MLe, MLc
            0x04,
            0x06,
            0xE1,
            0x04, // NDEF file control TLV: file E104
            (NDEF_FILE_SIZE >> 8) as u8,
            (NDEF_FILE_SIZE & 0xFF) as u8, // maximum NDEF file size
            0x00,
            0x00, // read and write access: open
        ];
        Self { cc, ndef: [0; NDEF_FILE_SIZE], selected: Selected::None, app_selected: false, updates: 0 }
    }

    /// NLEN + message.
    pub fn ndef_file(&self) -> &[u8; NDEF_FILE_SIZE] {
        &self.ndef
    }

    pub fn update_count(&self) -> usize {
        self.updates
    }
}

impl Default for Type4NdefApp {
    fn default() -> Self {
        Self::new()
    }
}

impl ApduApp for Type4NdefApp {
    fn reset(&mut self) {
        self.selected = Selected::None;
    }

    fn apdu(&mut self, c: &[u8], resp: &mut [u8]) -> usize {
        let n = c.len();
        if n < 4 || resp.len() < MAX_READ + 2 {
            return sw(resp, 0x67, 0x00);
        }
        if c[0] != 0x00 {
            return sw(resp, 0x6E, 0x00);
        }
        let (ins, p1, p2) = (c[1], c[2], c[3]);

        match ins {
            0xA4 => {
                // SELECT
                let lc = if n > 4 { c[4] as usize } else { 0 };
                if n >= 5 + lc && p1 == 0x04 {
                    // by name: the NDEF application
                    if lc == NDEF_AID.len() && c[5..5 + lc] == NDEF_AID {
                        self.app_selected = true;
                        self.selected = Selected::App;
                        return sw(resp, 0x90, 0x00);
                    }
                    return sw(resp, 0x6A, 0x82);
                }
                if p1 == 0x00 && lc == 2 && n >= 7 && self.app_selected {
                    // by file id
                    let id = (c[5] as u16) << 8 | c[6] as u16;
                    return match id {
                        0xE103 => {
                            self.selected = Selected::Cc;
                            sw(resp, 0x90, 0x00)
                        }
                        0xE104 => {
                            self.selected = Selected::NdefFile;
                            sw(resp, 0x90, 0x00)
                        }
                        _ => sw(resp, 0x6A, 0x82),
                    };
                }
                sw(resp, 0x6A, 0x86)
            }
            0xB0 => {
                // READ BINARY
                if self.selected != Selected::Cc && self.selected != Selected::NdefFile {
                    return sw(resp, 0x69, 0x86);
                }
                if p1 & 0x80 != 0 {
                    return sw(resp, 0x6B, 0x00);
                }
                let off = (p1 as usize) << 8 | p2 as usize;
                let mut le = if n > 4 { c[4] as usize } else { 0 };
                if le == 0 || le > MAX_READ {
                    le = MAX_READ;
                }
                let file: &[u8] = if self.selected == Selected::Cc { &self.cc } else { &self.ndef };
                if off > file.len() {
                    return sw(resp, 0x6B, 0x00);
                }
                if off + le > file.len() {
                    le = file.len() - off;
                }
                resp[..le].copy_from_slice(&file[off..off + le]);
                resp[le] = 0x90;
                resp[le + 1] = 0x00;
                le + 2
            }
            0xD6 => {
                // UPDATE BINARY
                if self.selected != Selected::NdefFile {
                    return if self.selected == Selected::Cc { sw(resp, 0x69, 0x82) } else { sw(resp, 0x69, 0x86) };
                }
                if p1 & 0x80 != 0 {
                    return sw(resp, 0x6B, 0x00);
                }
                let off = (p1 as usize) << 8 | p2 as usize;
                let lc = if n > 4 { c[4] as usize } else { 0 };
                if n != 5 + lc || lc == 0 || lc > MAX_READ {
                    return sw(resp, 0x67, 0x00);
                }
                if off + lc > NDEF_FILE_SIZE {
                    return sw(resp, 0x6B, 0x00);
                }
                self.ndef[off..off + lc].copy_from_slice(&c[5..5 + lc]);
                self.updates += 1;
                sw(resp, 0x90, 0x00)
            }
            _ => sw(resp, 0x6D, 0x00),
        }
    }
}

/// Records every APDU. The capture is a list of records:
/// `[1 = command, 2 = response][length hi][length lo][bytes]`.
pub struct SniffApp {
    ndef: Type4NdefApp,
    buf: [u8; CAPTURE_SIZE],
    used: usize,
    apdus: usize,
    sessions: usize,
    busy_left: u8,
    wtx: u8,
    full: bool,
}

impl SniffApp {
    pub fn new() -> Self {
        Self {
            ndef: Type4NdefApp::new(),
            buf: [0; CAPTURE_SIZE],
            used: 0,
            apdus: 0,
            sessions: 0,
            busy_left: 0,
            wtx: 0,
            full: false,
        }
    }

    pub fn capture(&self) -> &[u8] {
        &self.buf[..self.used]
    }

    pub fn apdu_count(&self) -> usize {
        self.apdus
    }

    pub fn session_count(&self) -> usize {
        self.sessions
    }

    pub fn full(&self) -> bool {
        self.full
    }

    pub fn clear(&mut self) {
        self.used = 0;
        self.apdus = 0;
        self.sessions = 0;
        self.busy_left = 0;
        self.wtx = 0;
        self.full = false;
    }

    fn record(&mut self, kind: u8, data: &[u8]) {
        if self.used + 3 + data.len() > CAPTURE_SIZE {
            self.full = true;
            return;
        }
        self.buf[self.used] = kind;
        self.buf[self.used + 1] = (data.len() >> 8) as u8;
        self.buf[self.used + 2] = data.len() as u8;
        self.buf[self.used + 3..self.used + 3 + data.len()].copy_from_slice(data);
        self.used += 3 + data.len();
    }
}

impl Default for SniffApp {
    fn default() -> Self {
        Self::new()
    }
}

impl ApduApp for SniffApp {
    fn reset(&mut self) {
        self.sessions += 1;
        self.ndef.reset();
        self.wtx = 0;
    }

    fn take_wtx_requests(&mut self) -> u8 {
        core::mem::take(&mut self.wtx)
    }

    fn apdu(&mut self, c: &[u8], resp: &mut [u8]) -> usize {
        self.apdus += 1;
        self.record(1, c);
        let n = c.len();
        if n >= 4 && c[0] == 0x00 {
            // class 00: a real NDEF tag, so the phone's background reader stops asking
            let rl = self.ndef.apdu(c, resp);
            self.record(2, &resp[..rl]);
            return rl;
        }
        if n >= 4 && c[0] == 0xF0 && c[1] == 0xD4 {
            // screen refresh starts
            self.busy_left = BUSY_POLLS;
            self.wtx = REFRESH_WTX_REQUESTS;
        }
        let rl;
        if n >= 4 && c[0] == 0xF0 && c[1] == 0xDE {
            // vendor "get busy": 01 busy, 00 idle
            resp[0] = if self.busy_left > 0 { 0x01 } else { 0x00 };
            if self.busy_left > 0 {
                self.busy_left -= 1;
            }
            resp[1] = 0x90;
            resp[2] = 0x00;
            rl = 3;
        } else {
            resp[0] = 0x90;
            resp[1] = 0x00;
            rl = 2;
        }
        self.record(2, &resp[..rl]);
        rl
    }
}
