//! NFC session on top of `rnfc-st25r39` (chip driver) and `rnfc` (ISO14443-A select).
//! Only Type 2 READ/WRITE and NTAG215 knowledge is ours.
use core::convert::Infallible;

use embassy_rp::i2c::I2c;
use embassy_rp::i2c::Blocking;
use embassy_rp::peripherals::I2C1;
use embedded_hal::digital::{ErrorType, InputPin};
use heapless::Vec;
use rnfc::iso14443a::Poller;
use rnfc::traits::iso14443a::UID_MAX_LEN;
use rnfc::traits::iso14443a_ll::{Error as LlError, ErrorKind, Frame, Reader as LlReader};
use rnfc_st25r39::iso14443a::Iso14443a;
use rnfc_st25r39::{I2cInterface, St25r39};

/// The unit has no IRQ line on its Grove connector. rnfc-st25r39 polls the IRQ registers and
/// only uses the pin in `wait_for_card`, so a pin that never fires is enough.
pub struct NoIrq;
impl ErrorType for NoIrq {
    type Error = Infallible;
}
impl InputPin for NoIrq {
    fn is_high(&mut self) -> Result<bool, Infallible> {
        Ok(false)
    }
    fn is_low(&mut self) -> Result<bool, Infallible> {
        Ok(true)
    }
}
impl embedded_hal_async::digital::Wait for NoIrq {
    async fn wait_for_high(&mut self) -> Result<(), Infallible> {
        core::future::pending().await
    }
    async fn wait_for_low(&mut self) -> Result<(), Infallible> {
        Ok(())
    }
    async fn wait_for_rising_edge(&mut self) -> Result<(), Infallible> {
        core::future::pending().await
    }
    async fn wait_for_falling_edge(&mut self) -> Result<(), Infallible> {
        core::future::pending().await
    }
    async fn wait_for_any_edge(&mut self) -> Result<(), Infallible> {
        core::future::pending().await
    }
}

pub type Iface = I2cInterface<I2c<'static, I2C1, Blocking>>;
pub type Rf = St25r39<Iface, NoIrq>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NfcError {
    /// No response from the card.
    Timeout,
    /// CRC / parity / framing / collision on receive.
    Frame,
    BadResponse,
    /// Chip or I2C failure.
    Chip,
    Param,
    NoAck,
    /// The tag answered a WRITE with a 4-bit NAK (code in the low nibble).
    Nack(u8),
    /// The tag did not run a read-only command (NAK, or it does not know it).
    Refused,
    /// Page is read-only, out of range, or lock/config/CC without force.
    Protected,
    Verify,
    TooLarge,
    NoCc,
    Ndef,
}

impl NfcError {
    pub fn text(self) -> &'static str {
        match self {
            Self::Timeout => "no response from card",
            Self::Frame => "receive error",
            Self::BadResponse => "bad response",
            Self::Chip => "chip or I2C error",
            Self::Param => "bad parameter",
            Self::NoAck => "no ACK",
            Self::Nack(code) => tag::nack_text(code),
            Self::Refused => "the tag refused the command or does not support it",
            Self::Protected => "page is protected (read-only, out of range, or one-way lock/config)",
            Self::Verify => "verify failed",
            Self::TooLarge => "message does not fit the tag",
            Self::NoCc => "capability container missing",
            Self::Ndef => "malformed NDEF data",
        }
    }
}

fn from_kind(k: ErrorKind) -> NfcError {
    match k {
        ErrorKind::Timeout => NfcError::Timeout,
        ErrorKind::Corruption => NfcError::Frame,
        _ => NfcError::Chip,
    }
}

fn from_ll<E: LlError>(e: E) -> NfcError {
    from_kind(e.kind())
}

/// One field-on session: the field is on while this lives and off after it is dropped, which
/// also power-cycles a tag that an earlier select left ACTIVE.
pub struct Session<'a> {
    ll: Iso14443a<'a, Iface, NoIrq>,
    pub uid: Vec<u8, UID_MAX_LEN>,
    pub atqa: [u8; 2],
    pub sak: u8,
}

pub const T2_READ: u8 = 0x30;
pub const T2_WRITE: u8 = 0xA2;
/// 5 ms and 10 ms in carrier clocks (13.56 MHz).
const READ_TIMEOUT_1FC: u32 = 65_536;
const WRITE_TIMEOUT_1FC: u32 = 135_600;

pub async fn open(rf: &mut Rf) -> Result<Session<'_>, NfcError> {
    let mut ll = rf.start_iso14443a().await.map_err(|_| NfcError::Chip)?;
    let (uid, atqa, sak) = {
        let mut poller = Poller::new(&mut ll);
        let card = poller.select_any().await.map_err(|e| match e {
            rnfc::iso14443a::Error::Lower(l) => from_ll(l),
            rnfc::iso14443a::Error::Protocol => NfcError::BadResponse,
        })?;
        use rnfc::traits::iso14443a::Reader;
        (Vec::from_slice(card.uid()).unwrap(), card.atqa(), card.sak())
    };
    Ok(Session { ll, uid, atqa, sak })
}

impl Session<'_> {
    /// READ returns 4 pages (16 bytes) starting at `page`.
    pub async fn t2_read(&mut self, page: u8) -> Result<[u8; 16], NfcError> {
        let mut rx = [0u8; 18];
        let bits = self
            .ll
            .transceive(&[T2_READ, page], &mut rx, Frame::Standard { timeout_1fc: READ_TIMEOUT_1FC })
            .await
            .map_err(from_ll)?;
        if bits != 16 * 8 {
            return Err(NfcError::BadResponse);
        }
        let mut out = [0u8; 16];
        out.copy_from_slice(&rx[..16]);
        Ok(out)
    }
}

use m5nfc::ndef::{self, NdefError, Record};
use m5nfc::tag;

impl From<NdefError> for NfcError {
    fn from(e: NdefError) -> Self {
        match e {
            NdefError::TooLarge => NfcError::TooLarge,
            NdefError::Malformed => NfcError::Ndef,
        }
    }
}

impl Session<'_> {
    /// WRITE one 4-byte page. The tag answers with a 4-bit ACK (0xA) without CRC, which needs the
    /// local patch to rnfc (see vendor/rnfc/PATCHES.md).
    pub async fn t2_write(&mut self, page: u8, data: [u8; 4]) -> Result<(), NfcError> {
        let tx = [T2_WRITE, page, data[0], data[1], data[2], data[3]];
        let mut rx = [0u8; 4];
        let bits = self
            .ll
            .transceive(&tx, &mut rx, Frame::StandardShortRx { timeout_1fc: WRITE_TIMEOUT_1FC })
            .await
            .map_err(from_ll)?;
        if bits != 4 {
            return Err(NfcError::BadResponse); // not a 4-bit ACK/NAK frame
        }
        match rx[0] & 0x0F {
            tag::ACK => Ok(()),
            code => Err(NfcError::Nack(code)),
        }
    }

    /// Guarded write with read-back verify where the page can be read back.
    pub async fn write_page(&mut self, page: u8, data: [u8; 4], force: bool, verify: bool) -> Result<(), NfcError> {
        if !tag::may_write(page, force) {
            return Err(NfcError::Protected);
        }
        self.t2_write(page, data).await?;
        if verify && tag::verifiable(page) {
            let got = self.t2_read(page).await?;
            if got[..4] != data {
                return Err(NfcError::Verify);
            }
        }
        Ok(())
    }

    /// 512 bytes (pages 4..131); only the first `USER_BYTES` are user memory.
    pub async fn read_user(&mut self) -> Result<[u8; tag::USER_READ_BYTES], NfcError> {
        let mut out = [0u8; tag::USER_READ_BYTES];
        for (i, page) in (tag::USER_FIRST..=tag::USER_LAST).step_by(4).enumerate() {
            out[i * 16..][..16].copy_from_slice(&self.t2_read(page).await?);
        }
        Ok(out)
    }

    pub async fn read_ndef(&mut self) -> Result<Vec<Record, { ndef::MAX_RECORDS }>, NfcError> {
        let user = self.read_user().await?;
        match ndef::find_message(&user[..tag::USER_BYTES]) {
            Some(m) if !m.is_empty() => Ok(ndef::decode_message(m)?),
            _ => Ok(Vec::new()),
        }
    }

    /// Writes the message; returns the number of pages written (identical pages are skipped).
    pub async fn write_ndef(&mut self, recs: &[Record]) -> Result<usize, NfcError> {
        let cc = self.t2_read(tag::PAGE_CC).await?;
        if cc[0] != 0xE1 {
            return Err(NfcError::NoCc);
        }
        let msg = ndef::encode_message::<{ tag::USER_BYTES }>(recs)?;
        let mut blob = ndef::wrap_tlv::<{ tag::USER_BYTES }>(&msg)?;
        while blob.len() % tag::PAGE_SIZE != 0 {
            let _ = blob.push(0); // pad to whole pages; fits because USER_BYTES is page-aligned
        }
        let current = self.read_user().await?;
        let mut count = 0;
        for (i, chunk) in blob.chunks(tag::PAGE_SIZE).enumerate() {
            if current[i * 4..][..4] != *chunk {
                let mut d = [0u8; 4];
                d.copy_from_slice(chunk);
                self.write_page(tag::USER_FIRST + i as u8, d, false, true).await?;
                count += 1;
            }
        }
        Ok(count)
    }
}

// ---- ISO-DEP (ISO 14443-4) and DESFire, using rnfc's tested ISO-DEP layer ------------------------

use rnfc::iso_dep::{Error as DepError, IsoDepA};
use rnfc::traits::iso_dep::Reader as DepReader;

/// What a DESFire card tells anyone without keys.
pub struct DesfireInfo {
    pub uid: Vec<u8, UID_MAX_LEN>,
    pub atqa: [u8; 2],
    pub sak: u8,
    pub ats: Vec<u8, 32>,
    /// GET_VERSION: hardware part (7), software part (7), UID/batch/production date (14).
    pub version: Vec<u8, 28>,
    pub version_sw: (u8, u8),
    /// GET_APPLICATION_IDS: 3 bytes per application, and the status the card ended with.
    pub app_ids: Vec<u8, 126>,
    pub app_sw: (u8, u8),
}

fn dep_err<E: LlError>(e: DepError<E>) -> NfcError {
    match e {
        DepError::Iso14443a(l) => from_ll(l),
        DepError::Communication => NfcError::Timeout,
        DepError::Protocol => NfcError::BadResponse,
        DepError::TxFrameTooBig | DepError::RxFrameTooBig => NfcError::Param,
    }
}

/// Runs one native DESFire command wrapped in an ISO 7816 APDU (`90 INS 00 00 00`), following
/// "additional frame" replies (`91 AF`). Appends the data to `out` and returns SW1 SW2.
async fn desfire_cmd<R: DepReader, const N: usize>(
    dep: &mut R,
    ins: u8,
    out: &mut Vec<u8, N>,
) -> Result<(u8, u8), NfcError>
where
    R::Error: core::fmt::Debug,
{
    let mut ins = ins;
    let mut rx = [0u8; 128];
    loop {
        let n = dep.transceive(&[0x90, ins, 0x00, 0x00, 0x00], &mut rx).await.map_err(|_| NfcError::Timeout)?;
        if n < 2 {
            return Err(NfcError::BadResponse);
        }
        out.extend_from_slice(&rx[..n - 2]).map_err(|_| NfcError::TooLarge)?;
        let sw = (rx[n - 2], rx[n - 1]);
        if sw == (0x91, 0xAF) {
            ins = 0xAF;
            continue;
        }
        return Ok(sw);
    }
}

pub async fn desfire_info(rf: &mut Rf) -> Result<DesfireInfo, NfcError> {
    let mut ll = rf.start_iso14443a().await.map_err(|_| NfcError::Chip)?;
    let mut poller = Poller::new(&mut ll);
    let card = poller.select_any().await.map_err(|e| match e {
        rnfc::iso14443a::Error::Lower(l) => from_ll(l),
        rnfc::iso14443a::Error::Protocol => NfcError::BadResponse,
    })?;
    use rnfc::traits::iso14443a::Reader as A;
    let (uid, atqa, sak) = (Vec::from_slice(card.uid()).unwrap(), card.atqa(), card.sak());
    let mut dep = IsoDepA::new(card).await.map_err(dep_err)?;
    let ats = Vec::from_slice(dep.ats()).unwrap_or_default();

    let mut version: Vec<u8, 28> = Vec::new();
    let version_sw = desfire_cmd(&mut dep, 0x60, &mut version).await?;
    let mut app_ids: Vec<u8, 126> = Vec::new();
    let app_sw = desfire_cmd(&mut dep, 0x6A, &mut app_ids).await?;
    let _ = dep.deselect().await;
    Ok(DesfireInfo { uid, atqa, sak, ats, version, version_sw, app_ids, app_sw })
}

// ---- NTAG identification (NTAG21x datasheet 10.1, 10.6, 10.8) ---------------------------------

/// The read-only NTAG commands. A tag that does not know one stays silent or answers with a
/// 4-bit NAK, which the receive path reports as a receive error.
#[derive(Clone, Copy)]
pub enum Ident {
    Version,
    Signature,
    Counter,
}

impl Ident {
    fn bytes(self, addr: Option<u8>) -> ([u8; 2], usize, usize) {
        match self {
            Ident::Version => ([0x60, 0], 1, 8),
            Ident::Signature => ([0x3C, addr.unwrap_or(0x00)], 2, 32),
            Ident::Counter => ([0x39, addr.unwrap_or(0x02)], 2, 3),
        }
    }
}

impl Session<'_> {
    async fn ident_raw(&mut self, which: Ident, addr: Option<u8>) -> Result<Vec<u8, 32>, NfcError> {
        let (tx, len, want) = which.bytes(addr);
        let mut rx = [0u8; 40]; // room for the CRC_A before it is stripped
        let bits = self
            .ll
            .transceive(&tx[..len], &mut rx, Frame::Standard { timeout_1fc: READ_TIMEOUT_1FC })
            .await
            .map_err(from_ll)?;
        if bits != want * 8 {
            return Err(NfcError::BadResponse);
        }
        Ok(Vec::from_slice(&rx[..want]).unwrap())
    }
}

/// Selects the tag and runs one identification command. If the tag does not answer, it is
/// selected again: a tag that still answers refused the command, a missing tag is absent.
pub async fn ident(rf: &mut Rf, which: Ident, addr: Option<u8>) -> Result<Vec<u8, 32>, NfcError> {
    let first = {
        let mut s = open(rf).await?;
        s.ident_raw(which, addr).await
    };
    match first {
        Err(NfcError::Timeout) | Err(NfcError::Frame) | Err(NfcError::BadResponse) => {
            open(rf).await?; // still there?
            Err(NfcError::Refused)
        }
        other => other,
    }
}
