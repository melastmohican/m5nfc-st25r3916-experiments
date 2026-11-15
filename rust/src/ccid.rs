//! CCID message layer, independent of USB and of the NFC chip. Port of `../tinygo/ccid/proto.go`.
//! `Slot::handle` turns one PC_to_RDR bulk message into the RDR_to_PC reply, talking to a `Backend`.
use heapless::Vec;

// PC_to_RDR
pub const ICC_POWER_ON: u8 = 0x62;
pub const ICC_POWER_OFF: u8 = 0x63;
pub const GET_SLOT_STATUS: u8 = 0x65;
pub const XFR_BLOCK: u8 = 0x6F;
pub const GET_PARAMETERS: u8 = 0x6C;
pub const SET_PARAMETERS: u8 = 0x61;
pub const RESET_PARAMETERS: u8 = 0x6D;
pub const ESCAPE: u8 = 0x6B;
// RDR_to_PC
pub const DATA_BLOCK: u8 = 0x80;
pub const SLOT_STATUS: u8 = 0x81;
pub const PARAMETERS: u8 = 0x82;
pub const ESCAPE_RESP: u8 = 0x83;
pub const NOTIFY_SLOT_CHANGE: u8 = 0x50;

// bStatus: bits 0-1 = ICC status, bits 6-7 = command status (1 = failed)
pub const ICC_ACTIVE: u8 = 0;
pub const ICC_INACTIVE: u8 = 1;
pub const ICC_ABSENT: u8 = 2;
pub const CMD_OK: u8 = 0x00;
pub const CMD_FAILED: u8 = 0x40;

const ERR_CMD_NOT_SUPPORTED: u8 = 0x00;
const ERR_SLOT: u8 = 0x05;
const ERR_ICC_MUTE: u8 = 0xFE;
const ERR_HW: u8 = 0xFB;

pub const HEADER_LEN: usize = 10;
/// Header + 261 bytes, as announced in the class descriptor.
pub const MAX_MESSAGE: usize = 271;
pub const MAX_DATA: usize = MAX_MESSAGE - HEADER_LEN;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BeError {
    /// The card is gone (or never answered).
    Absent,
    /// Chip or I2C failure.
    Hw,
}

#[allow(async_fn_in_trait)]
pub trait Backend {
    /// `ICC_ACTIVE` / `ICC_INACTIVE` / `ICC_ABSENT`.
    async fn status(&mut self) -> Result<u8, BeError>;
    async fn power_on(&mut self) -> Result<Vec<u8, 32>, BeError>;
    async fn power_off(&mut self);
    /// APDU in, response (including SW1 SW2) out.
    async fn transmit(&mut self, apdu: &[u8], resp: &mut Vec<u8, MAX_DATA>) -> Result<(), BeError>;
}

/// PC/SC part 3 ATR for an ISO 14443-A part 3 card (MIFARE Ultralight family): 20 bytes.
pub fn contactless_atr() -> Vec<u8, 32> {
    const BODY: [u8; 18] = [
        0x8F, 0x80, 0x01, 0x80, 0x4F, 0x0C, 0xA0, 0x00, 0x00, 0x03, 0x06, 0x03, 0x00, 0x03, 0x00, 0x00,
        0x00, 0x00,
    ];
    let mut out = Vec::new();
    let _ = out.push(0x3B);
    let _ = out.extend_from_slice(&BODY);
    let _ = out.push(BODY.iter().fold(0, |a, b| a ^ b));
    out
}

/// RDR_to_PC_NotifySlotChange: 2 bytes.
pub fn slot_change_message(present: bool, changed: bool) -> [u8; 2] {
    [NOTIFY_SLOT_CHANGE, present as u8 | (changed as u8) << 1]
}

type Reply = Vec<u8, MAX_MESSAGE>;

fn reply(typ: u8, slot: u8, seq: u8, status: u8, error: u8, specific: u8, data: &[u8]) -> Reply {
    let n = data.len();
    let mut out = Reply::new();
    let _ = out.extend_from_slice(&[typ, n as u8, (n >> 8) as u8, 0, 0, slot, seq, status, error, specific]);
    let _ = out.extend_from_slice(data);
    out
}

fn failed(typ: u8, slot: u8, seq: u8, icc: u8, error: u8) -> Reply {
    reply(typ, slot, seq, CMD_FAILED | icc, error, 0, &[])
}

pub struct Slot;

impl Slot {
    async fn icc_status<B: Backend>(be: &mut B) -> u8 {
        be.status().await.unwrap_or(ICC_ABSENT)
    }

    /// Returns the reply for one message (`None` = no reply).
    pub async fn handle<B: Backend>(&mut self, be: &mut B, msg: &[u8]) -> Option<Reply> {
        if msg.len() < HEADER_LEN {
            return None;
        }
        let (mtype, slot, seq) = (msg[0], msg[5], msg[6]);
        let length = (msg[1] as usize | (msg[2] as usize) << 8 | (msg[3] as usize) << 16 | (msg[4] as usize) << 24)
            .min(msg.len() - HEADER_LEN);
        let payload = &msg[HEADER_LEN..HEADER_LEN + length];

        let data_type = match mtype {
            ICC_POWER_ON | XFR_BLOCK => DATA_BLOCK,
            ESCAPE => ESCAPE_RESP,
            GET_PARAMETERS | SET_PARAMETERS | RESET_PARAMETERS => PARAMETERS,
            _ => SLOT_STATUS,
        };
        if slot != 0 {
            return Some(failed(data_type, slot, seq, ICC_ABSENT, ERR_SLOT));
        }

        Some(match mtype {
            GET_SLOT_STATUS => match be.status().await {
                Ok(s) => reply(SLOT_STATUS, slot, seq, CMD_OK | s, 0, 0, &[]),
                Err(_) => failed(SLOT_STATUS, slot, seq, ICC_ABSENT, ERR_HW),
            },
            ICC_POWER_ON => match be.power_on().await {
                Ok(atr) => reply(DATA_BLOCK, slot, seq, CMD_OK | ICC_ACTIVE, 0, 0, &atr),
                Err(BeError::Absent) => failed(DATA_BLOCK, slot, seq, ICC_ABSENT, ERR_ICC_MUTE),
                Err(BeError::Hw) => failed(DATA_BLOCK, slot, seq, Self::icc_status(be).await, ERR_HW),
            },
            ICC_POWER_OFF => {
                be.power_off().await;
                reply(SLOT_STATUS, slot, seq, CMD_OK | Self::icc_status(be).await, 0, 0, &[])
            }
            XFR_BLOCK => match be.status().await {
                Err(_) => failed(DATA_BLOCK, slot, seq, ICC_ABSENT, ERR_HW),
                Ok(ICC_ABSENT) => failed(DATA_BLOCK, slot, seq, ICC_ABSENT, ERR_ICC_MUTE),
                Ok(_) => {
                    let mut resp: Vec<u8, MAX_DATA> = Vec::new();
                    match be.transmit(payload, &mut resp).await {
                        Ok(()) => reply(DATA_BLOCK, slot, seq, CMD_OK | ICC_ACTIVE, 0, 0, &resp),
                        Err(BeError::Absent) => failed(DATA_BLOCK, slot, seq, ICC_ABSENT, ERR_ICC_MUTE),
                        Err(BeError::Hw) => failed(DATA_BLOCK, slot, seq, Self::icc_status(be).await, ERR_HW),
                    }
                }
            },
            GET_PARAMETERS | SET_PARAMETERS | RESET_PARAMETERS => {
                // T=1 defaults; contactless cards ignore them
                let params = [0x11, 0x10, 0x00, 0x45, 0x00, 0xFE, 0x00];
                reply(PARAMETERS, slot, seq, CMD_OK | Self::icc_status(be).await, 0, 1, &params)
            }
            _ => failed(data_type, slot, seq, Self::icc_status(be).await, ERR_CMD_NOT_SUPPORTED),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use embassy_futures::block_on;

    struct Fake {
        status: u8,
        resp: &'static [u8],
        powered: bool,
    }

    impl Backend for Fake {
        async fn status(&mut self) -> Result<u8, BeError> {
            Ok(self.status)
        }
        async fn power_on(&mut self) -> Result<Vec<u8, 32>, BeError> {
            self.powered = true;
            Ok(contactless_atr())
        }
        async fn power_off(&mut self) {
            self.powered = false;
        }
        async fn transmit(&mut self, _apdu: &[u8], resp: &mut Vec<u8, MAX_DATA>) -> Result<(), BeError> {
            resp.extend_from_slice(self.resp).map_err(|_| BeError::Hw)
        }
    }

    fn msg(t: u8, slot: u8, payload: &[u8]) -> Vec<u8, 64> {
        let mut m = Vec::new();
        m.extend_from_slice(&[t, payload.len() as u8, 0, 0, 0, slot, 7, 0, 0, 0]).unwrap();
        m.extend_from_slice(payload).unwrap();
        m
    }

    #[test]
    fn atr_is_20_bytes_with_valid_tck() {
        let a = contactless_atr();
        assert_eq!(a.len(), 20);
        assert_eq!(a[0], 0x3B);
        assert_eq!(a[1..].iter().fold(0, |x, b| x ^ b), 0);
    }

    #[test]
    fn slot_change() {
        assert_eq!(slot_change_message(true, true), [0x50, 3]);
        assert_eq!(slot_change_message(false, true), [0x50, 2]);
    }

    #[test]
    fn handles_commands() {
        block_on(async {
            let mut be = Fake { status: ICC_INACTIVE, resp: &[1, 2, 0x90, 0], powered: false };
            let mut slot = Slot;
            let r = slot.handle(&mut be, &msg(GET_SLOT_STATUS, 0, &[])).await.unwrap();
            assert_eq!((r[0], r[6], r[7]), (SLOT_STATUS, 7, ICC_INACTIVE));
            let r = slot.handle(&mut be, &msg(ICC_POWER_ON, 0, &[])).await.unwrap();
            assert_eq!((r[0], r.len()), (DATA_BLOCK, HEADER_LEN + 20));
            be.status = ICC_ACTIVE;
            let r = slot.handle(&mut be, &msg(XFR_BLOCK, 0, &[0xFF, 0xCA, 0, 0, 0])).await.unwrap();
            assert_eq!((r[1], &r[HEADER_LEN..]), (4, &[1u8, 2, 0x90, 0][..]));
            let r = slot.handle(&mut be, &msg(GET_SLOT_STATUS, 1, &[])).await.unwrap();
            assert_eq!((r[7], r[8]), (CMD_FAILED | ICC_ABSENT, ERR_SLOT));
            let r = slot.handle(&mut be, &msg(0x99, 0, &[])).await.unwrap();
            assert!(r[7] & CMD_FAILED != 0);
            be.status = ICC_ABSENT;
            let r = slot.handle(&mut be, &msg(XFR_BLOCK, 0, &[1, 2, 3, 4])).await.unwrap();
            assert_eq!(r[8], ERR_ICC_MUTE);
            assert!(slot.handle(&mut be, &[1, 2]).await.is_none());
            slot.handle(&mut be, &msg(ICC_POWER_OFF, 0, &[])).await.unwrap();
            assert!(!be.powered);
        });
    }
}
