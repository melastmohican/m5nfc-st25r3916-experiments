//! The `emulate` and `emu-dump` commands: act as a tag and show what the reader did to it. Same
//! behaviour as `../arduino/src/Cli.cpp`. The state machine is `m5nfc::emu::listener`; this file
//! connects it to the chip and to the serial command line.
use core::fmt::Write as _;
use core::task::Poll;

use embassy_futures::{poll_once, yield_now};
use embassy_time::Instant;
use heapless::String;
use m5nfc::emu::apps::{SniffApp, Type4NdefApp, NDEF_FILE_SIZE};
use m5nfc::emu::isodep::IsoDepTarget;
use m5nfc::emu::listener::{Chip, Listener};
use m5nfc::emu::type2tag::{Type2Tag, BYTES, PAGES};
use m5nfc::emu::Target;
use m5nfc::ndef;
use rnfc_st25r39::Interface;

use crate::cli::{Cdc, Cli};
use crate::nfc::{Iface, NfcError, Rf};

const UID: [u8; 7] = [0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66];

/// The listener's view of the chip: rnfc's `Interface`, with errors turned into a flag.
struct IfaceChip<'a> {
    iface: &'a mut Iface,
    error: bool,
}

impl IfaceChip<'_> {
    fn note<T>(&mut self, r: Result<T, <Iface as Interface>::Error>) -> Option<T> {
        match r {
            Ok(v) => Some(v),
            Err(_) => {
                self.error = true;
                None
            }
        }
    }
}

impl Chip for IfaceChip<'_> {
    fn command(&mut self, cmd: u8) {
        let r = self.iface.do_command(cmd);
        self.note(r);
    }
    fn read_reg(&mut self, reg: u8) -> u8 {
        let r = self.iface.read_reg(reg);
        self.note(r).unwrap_or(0)
    }
    fn read_regs(&mut self, reg: u8, out: &mut [u8]) {
        let r = self.iface.read_regs(reg, out);
        if self.note(r).is_none() {
            out.fill(0);
        }
    }
    fn write_reg(&mut self, reg: u8, val: u8) {
        let r = self.iface.write_reg(reg, val);
        self.note(r);
    }
    fn read_fifo(&mut self, out: &mut [u8]) {
        let r = self.iface.read_fifo(out);
        if self.note(r).is_none() {
            out.fill(0);
        }
    }
    fn write_fifo(&mut self, data: &[u8]) {
        let r = self.iface.write_fifo(data);
        self.note(r);
    }
    fn write_pt_memory_a(&mut self, data: &[u8]) {
        let r = self.iface.write_pt_memory_a(data);
        self.note(r);
    }
    fn now_us(&mut self) -> u32 {
        Instant::now().as_micros() as u32
    }
    fn take_io_error(&mut self) -> bool {
        core::mem::take(&mut self.error)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    None,
    T2,
    T4,
    Sniff,
}

/// Everything `emulate` leaves behind for `emu-dump`: about 27 KB, mostly the sniffer's capture.
pub struct EmuState {
    tag: Type2Tag,
    t4: IsoDepTarget<Type4NdefApp>,
    sniff: IsoDepTarget<SniffApp>,
    listener: Listener,
    mode: Mode,
}

impl EmuState {
    pub fn new() -> Self {
        Self {
            tag: Type2Tag::new(UID),
            t4: IsoDepTarget::new(Type4NdefApp::new()),
            sniff: IsoDepTarget::new(SniffApp::new()),
            listener: Listener::new(),
            mode: Mode::None,
        }
    }
}

/// True if a byte arrived on the serial port (the byte is consumed).
fn byte_waiting(cdc: &mut Cdc) -> bool {
    let mut buf = [0u8; 64];
    matches!(poll_once(cdc.read_packet(&mut buf)), Poll::Ready(Ok(n)) if n > 0)
}

/// Runs the listener until `seconds` have passed or a byte arrives on the serial port. No output
/// while it runs: a reader gives a tag about 5 ms to answer.
async fn run<T: Target>(
    rf: &mut Rf,
    listener: &mut Listener,
    target: &mut T,
    cdc: &mut Cdc,
    seconds: u64,
    sak: u8,
) -> Result<(), NfcError> {
    // The chip is in reader mode with its oscillator on; the listener takes over from here.
    rf.raw().field_on().await.map_err(|_| NfcError::Chip)?;
    let started = {
        let mut raw = rf.raw();
        let mut chip = IfaceChip { iface: raw.interface(), error: false };
        listener.start(&mut chip, target, &UID, 0x0044, sak)
    };
    if !started {
        let _ = rf.raw().field_off().await;
        return Err(NfcError::Chip);
    }
    let t0 = Instant::now();
    let mut spins = 0u32;
    while t0.elapsed().as_secs() < seconds {
        {
            let mut raw = rf.raw();
            let mut chip = IfaceChip { iface: raw.interface(), error: false };
            listener.poll(&mut chip, target);
        }
        spins = spins.wrapping_add(1);
        if spins & 0x3F == 0 && byte_waiting(cdc) {
            break;
        }
        yield_now().await; // let the USB task run
    }
    // back to reader mode: the field off, so a phone nearby does not open Wallet
    {
        let mut raw = rf.raw();
        let mut chip = IfaceChip { iface: raw.interface(), error: false };
        chip.command(0xC2); // stop
    }
    rf.raw().field_off().await.map_err(|_| NfcError::Chip)
}

impl Cli {
    pub(crate) async fn emulate(&mut self, rf: &mut Rf, args: &[&str]) -> Result<(), NfcError> {
        let mut seconds = 30u64;
        let mut mode = Mode::T2;
        for a in args {
            match *a {
                "t2" => mode = Mode::T2,
                "t4" => mode = Mode::T4,
                "sniff" => mode = Mode::Sniff,
                n => seconds = n.parse().map_err(|_| NfcError::Param)?,
            }
        }
        if !(1..=600).contains(&seconds) {
            return Err(NfcError::Param);
        }
        let (what, sak) = match mode {
            Mode::T4 => ("a Type 4 NDEF tag", 0x20), // ISO-DEP: the reader sends RATS
            Mode::Sniff => ("an ISO-DEP card that logs every APDU", 0x20),
            _ => ("an NTAG215", 0x00),
        };
        // the line ending of this command (CR LF) must not count as the byte that ends the run
        while byte_waiting(&mut self.cdc) {}
        self.print(format_args!(
            "emulating {}, UID 04112233445566, for {} s: hold the phone to the unit (any byte ends it)\r\n",
            what, seconds
        ))
        .await;
        let r = {
            let Cli { cdc, emu, .. } = self;
            emu.mode = mode;
            match mode {
                Mode::Sniff => {
                    emu.sniff.app.clear();
                    run(rf, &mut emu.listener, &mut emu.sniff, cdc, seconds, sak).await
                }
                Mode::T4 => run(rf, &mut emu.listener, &mut emu.t4, cdc, seconds, sak).await,
                _ => run(rf, &mut emu.listener, &mut emu.tag, cdc, seconds, sak).await,
            }
        };
        // print the event log, then the totals
        let n = self.emu.listener.log_len();
        if self.emu.listener.log_dropped() > 0 {
            self.print(format_args!("({} older events dropped)\r\n", self.emu.listener.log_dropped())).await;
        }
        for i in 0..n {
            let mut line: String<128> = String::new();
            self.emu.listener.log_line(i, &mut line);
            let _ = line.push_str("\r\n");
            let _ = self.write(line.as_bytes()).await;
        }
        let (fields, frames) = (self.emu.listener.field_count(), self.emu.listener.frame_count());
        let mut tail: String<160> = String::new();
        let _ = write!(tail, "fields seen: {}, frames answered: {}", fields, frames);
        match mode {
            Mode::T2 => {
                let _ = write!(tail, ", tag writes: {}", self.emu.tag.write_count());
            }
            Mode::T4 => {
                let _ = write!(tail, ", NDEF file updates: {}", self.emu.t4.app.update_count());
            }
            _ => {
                let _ = write!(
                    tail,
                    ", APDUs: {}, sessions: {}",
                    self.emu.sniff.app.apdu_count(),
                    self.emu.sniff.app.session_count()
                );
            }
        }
        let _ = tail.push_str("\r\n");
        let _ = self.write(tail.as_bytes()).await;
        r
    }

    /// What the last `emulate` run captured: the NTAG215 memory in the format of `dump` (t2), the NDEF
    /// file (t4) or every APDU in full (sniff).
    pub(crate) async fn emu_dump(&mut self) -> Result<(), NfcError> {
        match self.emu.mode {
            Mode::None => {
                self.print(format_args!("nothing captured: run `emulate` first\r\n")).await;
                Ok(())
            }
            Mode::Sniff => {
                self.dump_apdus().await;
                Ok(())
            }
            Mode::T4 => {
                let f = *self.emu.t4.app.ndef_file();
                let nlen = (f[0] as usize) << 8 | f[1] as usize;
                self.print(format_args!("NDEF file: NLEN = {}, {} update(s)\r\n", nlen, self.emu.t4.app.update_count()))
                    .await;
                if nlen == 0 || nlen > NDEF_FILE_SIZE - 2 {
                    self.print(format_args!("no NDEF message\r\n")).await;
                    return Ok(());
                }
                for i in (0..nlen + 2).step_by(16) {
                    let mut line: String<80> = String::new();
                    let _ = write!(line, "{:04X} ", i);
                    for b in &f[i..(i + 16).min(nlen + 2)] {
                        let _ = write!(line, " {:02X}", b);
                    }
                    let _ = line.push_str("\r\n");
                    let _ = self.write(line.as_bytes()).await;
                }
                self.print_records(&f[2..2 + nlen]).await
            }
            Mode::T2 => {
                let mem = *self.emu.tag.memory();
                for page in 0..PAGES {
                    let c = &mem[page * 4..page * 4 + 4];
                    let mut t = [b'.'; 4];
                    for (i, &x) in c.iter().enumerate() {
                        if (32..127).contains(&x) {
                            t[i] = x;
                        }
                    }
                    let written = if self.emu.tag.changed(page) { "  (written)" } else { "" };
                    self.print(format_args!(
                        "{:3}  {:02X} {:02X} {:02X} {:02X}  {}{}\r\n",
                        page,
                        c[0],
                        c[1],
                        c[2],
                        c[3],
                        core::str::from_utf8(&t).unwrap_or("...."),
                        written
                    ))
                    .await;
                }
                match ndef::find_message(&mem[16..BYTES]) {
                    Some(m) if !m.is_empty() => self.print_records(m).await,
                    _ => {
                        self.print(format_args!("no NDEF message\r\n")).await;
                        Ok(())
                    }
                }
            }
        }
    }

    async fn print_records(&mut self, msg: &[u8]) -> Result<(), NfcError> {
        let recs = ndef::decode_message(msg).map_err(|_| NfcError::BadResponse)?;
        for r in &recs {
            let mut s: String<300> = String::new();
            r.describe(&mut s);
            let _ = s.push_str("\r\n");
            let _ = self.write(s.as_bytes()).await;
        }
        Ok(())
    }

    /// "C> <hex>" for a command the reader sent, "R> <hex>" for our answer, one APDU per line.
    async fn dump_apdus(&mut self) {
        let (apdus, sessions, full, total) = {
            let s = &self.emu.sniff.app;
            (s.apdu_count(), s.session_count(), s.full(), s.capture().len())
        };
        // the capture lives in `self`, and printing needs `&mut self`: copy it out record by record
        let mut pos = 0;
        self.print(format_args!(
            "APDUs captured: {} in {} session(s){}\r\n",
            apdus,
            sessions,
            if full { " (capture buffer full, later APDUs lost)" } else { "" }
        ))
        .await;
        while pos + 3 <= total {
            let (kind, len, chunk): (u8, usize, heapless::Vec<u8, 260>) = {
                let cap = self.emu.sniff.app.capture();
                let len = (cap[pos + 1] as usize) << 8 | cap[pos + 2] as usize;
                let mut v = heapless::Vec::new();
                let _ = v.extend_from_slice(&cap[pos + 3..(pos + 3 + len).min(pos + 3 + 260).min(cap.len())]);
                (cap[pos], len, v)
            };
            let mut line: String<800> = String::new();
            let _ = line.push_str(if kind == 1 { "C>" } else { "R>" });
            for b in &chunk {
                let _ = write!(line, " {:02X}", b);
            }
            if chunk.len() < len {
                let _ = line.push_str(" ...");
            }
            let _ = line.push_str("\r\n");
            let _ = self.write(line.as_bytes()).await;
            pos += 3 + len;
        }
    }
}
