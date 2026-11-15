//! CCID backend on the NFC unit: maps PC/SC pseudo-APDUs onto an NTAG tag.
//!
//!   FF CA 00 00 Le          -> UID (Le = 0: whole UID)
//!   FF B0 00 <page> <Le>    -> read Le bytes starting at <page>  (Le = 0: 16 bytes)
//!   FF D6 00 <page> 04 <d>  -> write one 4-byte page
//!
//! Lock/config pages are refused (guard in `m5nfc::tag`) with SW 69 82.
//!
//! Each call opens its own field-on session and re-selects the tag, so a tag left ACTIVE by an
//! earlier call is power-cycled. "Powered" means the host called PowerOn and the same UID still
//! answers.
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::mutex::Mutex;
use heapless::Vec;
use m5nfc::ccid::{self, BeError, Backend, MAX_DATA};
use m5nfc::tag;

use crate::isodep::{self, Cmd, Resp};
use crate::nfc::{self, NfcError, Rf};

pub type SharedRf = Mutex<CriticalSectionRawMutex, Rf>;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    /// Type 2 tag: every call opens its own session and re-selects.
    Type2,
    /// ISO-DEP card (DESFire): the isodep task keeps it activated between APDUs.
    IsoDep,
}

pub struct NfcBackend {
    rf: &'static SharedRf,
    powered: bool,
    mode: Mode,
    uid: Vec<u8, 10>,
}

impl NfcBackend {
    /// Closes an open ISO-DEP session (deselects the card and releases the chip).
    async fn end_isodep(&mut self) {
        if self.mode == Mode::IsoDep {
            self.mode = Mode::Type2;
            if isodep::OPEN.load(core::sync::atomic::Ordering::Acquire) {
                isodep::CMD.send(Cmd::Close).await;
            }
        }
    }

    /// Passes an APDU of any class to an ISO-DEP card (no `FF` pseudo-APDU translation).
    async fn transmit_isodep(&mut self, apdu: &[u8], resp: &mut Vec<u8, MAX_DATA>) -> Result<(), BeError> {
        if !isodep::OPEN.load(core::sync::atomic::Ordering::Acquire) {
            self.powered = false;
            return Err(BeError::Absent);
        }
        let Ok(cmd) = Vec::from_slice(apdu) else { return Err(BeError::Hw) };
        isodep::CMD.send(Cmd::Transmit(cmd)).await;
        match isodep::RESP.receive().await {
            Resp::Data(Ok(v)) => resp.extend_from_slice(&v).map_err(|_| BeError::Hw),
            Resp::Data(Err(e)) => {
                self.powered = false;
                Err(e)
            }
            Resp::Opened(_) => Err(BeError::Hw),
        }
    }

    pub fn new(rf: &'static SharedRf) -> Self {
        Self { rf, powered: false, mode: Mode::Type2, uid: Vec::new() }
    }
}

/// Is a card on the reader right now?
pub async fn card_present(rf: &'static SharedRf) -> bool {
    if isodep::OPEN.load(core::sync::atomic::Ordering::Acquire) {
        return true; // an ISO-DEP session holds the chip; it ends itself if the card goes away
    }
    let mut g = rf.lock().await;
    nfc::open(&mut g).await.is_ok()
}

fn sw(resp: &mut Vec<u8, MAX_DATA>, a: u8, b: u8) {
    let _ = resp.extend_from_slice(&[a, b]);
}

fn soft_absent(e: NfcError) -> bool {
    matches!(e, NfcError::Timeout | NfcError::Frame | NfcError::BadResponse)
}

impl Backend for NfcBackend {
    async fn status(&mut self) -> Result<u8, BeError> {
        if self.powered && self.mode == Mode::IsoDep {
            if isodep::OPEN.load(core::sync::atomic::Ordering::Acquire) {
                return Ok(ccid::ICC_ACTIVE);
            }
            self.powered = false;
            return Ok(ccid::ICC_ABSENT);
        }
        let mut g = self.rf.lock().await;
        match nfc::open(&mut g).await {
            Ok(s) if self.powered && s.uid == self.uid => Ok(ccid::ICC_ACTIVE),
            Ok(_) if self.powered => {
                self.powered = false; // a different card
                Ok(ccid::ICC_ABSENT)
            }
            Ok(_) => Ok(ccid::ICC_INACTIVE),
            Err(e) if soft_absent(e) => {
                self.powered = false;
                Ok(ccid::ICC_ABSENT)
            }
            Err(_) => Err(BeError::Hw),
        }
    }

    async fn power_on(&mut self) -> Result<Vec<u8, 32>, BeError> {
        self.end_isodep().await;
        self.powered = false;
        let mut g = self.rf.lock().await;
        let (uid, iso_dep) = match nfc::open(&mut g).await {
            Ok(s) => (s.uid.clone(), s.sak & 0x20 != 0),
            Err(e) if soft_absent(e) => return Err(BeError::Absent),
            Err(_) => return Err(BeError::Hw),
        };
        drop(g);
        self.uid = uid;
        if !iso_dep {
            self.mode = Mode::Type2;
            self.powered = true;
            return Ok(ccid::contactless_atr());
        }
        // ISO 14443-4 card: activate it for good and answer with an ATR built from its ATS.
        isodep::CMD.send(Cmd::Open).await;
        match isodep::RESP.receive().await {
            Resp::Opened(Ok(o)) => {
                self.mode = Mode::IsoDep;
                self.powered = true;
                Ok(ccid::atr_from_ats(&o.ats).unwrap_or_else(ccid::contactless_atr))
            }
            Resp::Opened(Err(e)) => Err(e),
            Resp::Data(_) => Err(BeError::Hw),
        }
    }

    async fn power_off(&mut self) {
        self.end_isodep().await;
        self.powered = false;
    }

    async fn transmit(&mut self, apdu: &[u8], resp: &mut Vec<u8, MAX_DATA>) -> Result<(), BeError> {
        if !self.powered {
            return Err(BeError::Absent);
        }
        if self.mode == Mode::IsoDep {
            return self.transmit_isodep(apdu, resp).await;
        }
        if apdu.len() < 4 {
            sw(resp, 0x67, 0x00);
            return Ok(());
        }
        if apdu[0] != 0xFF {
            sw(resp, 0x6E, 0x00); // class not supported
            return Ok(());
        }
        let mut g = self.rf.lock().await;
        let mut s = match nfc::open(&mut g).await {
            Ok(s) if s.uid == self.uid => s,
            Ok(_) => {
                self.powered = false;
                return Err(BeError::Absent);
            }
            Err(e) if soft_absent(e) => {
                self.powered = false;
                return Err(BeError::Absent);
            }
            Err(_) => return Err(BeError::Hw),
        };
        let le = apdu.get(4).copied().unwrap_or(0) as usize;
        let r: Result<(), NfcError> = async {
            match apdu[1] {
                0xCA => {
                    if apdu[2] != 0 {
                        sw(resp, 0x6B, 0x00);
                        return Ok(());
                    }
                    let take = if le == 0 || le > s.uid.len() { s.uid.len() } else { le };
                    let _ = resp.extend_from_slice(&s.uid[..take]);
                    sw(resp, 0x90, 0x00);
                }
                0xB0 => {
                    let page = apdu[3] as usize;
                    let n = if le == 0 { 16 } else { le };
                    if page >= tag::TOTAL_PAGES {
                        sw(resp, 0x6A, 0x82);
                        return Ok(());
                    }
                    let mut p = page;
                    while resp.len() < n && p < tag::TOTAL_PAGES {
                        let buf = s.t2_read(p as u8).await?;
                        let chunk = (n - resp.len()).min(16);
                        let _ = resp.extend_from_slice(&buf[..chunk]);
                        p += 4;
                    }
                    sw(resp, 0x90, 0x00);
                }
                0xD6 => {
                    if apdu.len() < 9 || apdu[4] != 4 {
                        sw(resp, 0x67, 0x00);
                        return Ok(());
                    }
                    let data = [apdu[5], apdu[6], apdu[7], apdu[8]];
                    match s.write_page(apdu[3], data, false, true).await {
                        Ok(()) => sw(resp, 0x90, 0x00),
                        Err(NfcError::Protected) => sw(resp, 0x69, 0x82),
                        Err(NfcError::Nack(0)) => sw(resp, 0x6A, 0x82), // tag: bad page address
                        Err(NfcError::Timeout) => return Err(NfcError::Timeout),
                        Err(_) => sw(resp, 0x65, 0x81), // memory failure
                    }
                }
                _ => sw(resp, 0x6D, 0x00), // instruction not supported
            }
            Ok(())
        }
        .await;
        match r {
            Ok(()) => Ok(()),
            Err(NfcError::Timeout) => {
                self.powered = false; // the tag was lifted
                Err(BeError::Absent)
            }
            Err(_) => Err(BeError::Hw),
        }
    }
}
