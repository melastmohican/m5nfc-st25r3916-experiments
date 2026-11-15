//! Serial command line, same commands as the other variants. Every command ends with a line
//! `ok` or `error: <reason>`.
use core::fmt::Write as _;

use embassy_rp::usb::Driver;
use embassy_time::Instant;
use embassy_usb::class::cdc_acm::CdcAcmClass;
use embassy_usb::driver::EndpointError;
use heapless::String;
use m5nfc::ndef;
use m5nfc::parse::{parse_hex4, tokenize};

use crate::backend::SharedRf;
use crate::nfc::{self, NfcError, Rf};

pub type Cdc = CdcAcmClass<'static, Driver<'static, embassy_rp::peripherals::USB>>;

pub const PAGE_COUNT: usize = 135;
const CC_NTAG215: [u8; 4] = [0xE1, 0x10, 0x3E, 0x00];

pub struct Cli {
    cdc: Cdc,
    ident: Option<u8>,
    rf: &'static SharedRf,
}

impl Cli {
    pub fn new(cdc: Cdc, ident: Option<u8>, rf: &'static SharedRf) -> Self {
        Self { cdc, ident, rf }
    }

    async fn write(&mut self, data: &[u8]) -> Result<(), EndpointError> {
        for chunk in data.chunks(64) {
            self.cdc.write_packet(chunk).await?;
        }
        if !data.is_empty() && data.len() % 64 == 0 {
            self.cdc.write_packet(&[]).await?;
        }
        Ok(())
    }

    async fn print(&mut self, args: core::fmt::Arguments<'_>) {
        let mut s: String<160> = String::new();
        let _ = s.write_fmt(args);
        let _ = self.write(s.as_bytes()).await;
    }

    pub async fn run(&mut self) -> ! {
        let mut buf = [0u8; 64];
        let mut line: heapless::Vec<u8, 200> = heapless::Vec::new();
        loop {
            self.cdc.wait_connection().await;
            while let Ok(n) = self.cdc.read_packet(&mut buf).await {
                for &b in &buf[..n] {
                    if b == b'\r' || b == b'\n' {
                        if !line.is_empty() {
                            if let Ok(text) = core::str::from_utf8(&line) {
                                let mut owned: String<200> = String::new();
                                let _ = owned.push_str(text);
                                self.exec(&owned).await;
                            }
                            line.clear();
                        }
                    } else {
                        let _ = line.push(b);
                    }
                }
            }
        }
    }

    async fn exec(&mut self, line: &str) {
        let shared = self.rf;
        // The chip is shared with the CCID reader; hold it for the whole command.
        let mut guard = shared.lock().await;
        let rf: &mut Rf = &mut guard;
        let argv = tokenize(line);
        let Some(&cmd) = argv.first() else { return };
        let r = match cmd {
            "chip" => self.chip().await,
            "detect" => self.detect(rf).await,
            "dump" => self.dump(rf).await,
            "read-ndef" => self.read_ndef(rf).await,
            "write-ndef" => self.write_ndef(rf, &argv[1..]).await,
            "write-page" => self.write_page(rf, &argv[1..]).await,
            "desfire" => self.desfire(rf).await,
            "cycles" => self.cycles(rf, argv.get(1).copied()).await,
            "bootsel" => {
                // Reboot into the RP2350 bootloader so `picotool load` works without the button.
                self.print(format_args!("rebooting to BOOTSEL\r\n")).await;
                embassy_time::Timer::after_millis(100).await;
                embassy_rp::rom_data::reset_to_usb_boot(0, 0);
                Ok(())
            }
            "help" => {
                self.print(format_args!("commands: chip | detect | dump | read-ndef | write-ndef [--text T] [--lang L] [--uri U] |\r\n          write-page <page> <8 hex> [--force] | desfire | cycles [N] | bootsel | help\r\n")).await;
                Ok(())
            }
            other => {
                self.print(format_args!("unknown command: {}\r\n", other)).await;
                Err(NfcError::Param)
            }
        };
        match r {
            Ok(()) => self.print(format_args!("ok\r\n")).await,
            Err(e) => self.print(format_args!("error: {}\r\n", e.text())).await,
        }
    }

    async fn chip(&mut self) -> Result<(), NfcError> {
        // IC identity: bits 7:3 type, bits 2:0 revision
        match self.ident {
            Some(v) if (v >> 3) & 0x1F == 0x05 && v & 7 != 0 => {
                self.print(format_args!("ST25R3916 present: type=0x{:02X} revision={}\r\n", v >> 3, v & 7)).await;
                Ok(())
            }
            _ => Err(NfcError::Chip),
        }
    }

    async fn detect(&mut self, rf: &mut Rf) -> Result<(), NfcError> {
        let mut s = nfc::open(rf).await?;
        let mut uid: String<40> = String::new();
        for (i, b) in s.uid.iter().enumerate() {
            let _ = write!(uid, "{}{:02X}", if i > 0 { ":" } else { "" }, b);
        }
        let (atqa, sak) = (s.atqa, s.sak);
        self.print(format_args!("UID:  {}\r\nATQA: {:02X}{:02X}  SAK: 0x{:02X}\r\n", uid, atqa[0], atqa[1], sak)).await;
        if sak & 0x20 != 0 {
            // SAK bit 6: the card speaks ISO 14443-4 and does not answer Type 2 READ.
            self.print(format_args!("Type: ISO-DEP card (ISO 14443-4); try `desfire`\r\n")).await;
            return Ok(());
        }
        let cc = s.t2_read(3).await?;
        if cc[..4] == CC_NTAG215 {
            self.print(format_args!("Type: NTAG215\r\n")).await;
        } else {
            self.print(format_args!("Type: not an NTAG215 (CC differs)\r\n")).await;
        }
        Ok(())
    }

    /// All 135 pages; pages 133 and 134 (PWD, PACK) read as zeros.
    async fn read_all(s: &mut nfc::Session<'_>, out: &mut [u8; PAGE_COUNT * 4]) -> Result<(), NfcError> {
        for page in (0u8..132).step_by(4) {
            let r = s.t2_read(page).await?;
            out[page as usize * 4..][..16].copy_from_slice(&r);
        }
        let r = s.t2_read(131).await?; // pages 131..134; 131 is already in the dump
        out[132 * 4..].copy_from_slice(&r[4..]);
        Ok(())
    }

    async fn dump(&mut self, rf: &mut Rf) -> Result<(), NfcError> {
        let mut data = [0u8; PAGE_COUNT * 4];
        {
            let mut s = nfc::open(rf).await?;
            Self::read_all(&mut s, &mut data).await?;
        }
        for p in 0..PAGE_COUNT {
            let c = &data[p * 4..][..4];
            let mut t = [b'.'; 4];
            for (i, &x) in c.iter().enumerate() {
                if (32..127).contains(&x) {
                    t[i] = x;
                }
            }
            self.print(format_args!(
                "{:3}  {:02X} {:02X} {:02X} {:02X}  {}\r\n",
                p,
                c[0],
                c[1],
                c[2],
                c[3],
                core::str::from_utf8(&t).unwrap_or("....")
            ))
            .await;
        }
        Ok(())
    }

    async fn read_ndef(&mut self, rf: &mut Rf) -> Result<(), NfcError> {
        let recs = nfc::open(rf).await?.read_ndef().await?;
        if recs.is_empty() {
            self.print(format_args!("no NDEF message\r\n")).await;
        }
        for r in &recs {
            let mut s: String<300> = String::new();
            r.describe(&mut s);
            let _ = s.push_str("\r\n");
            let _ = self.write(s.as_bytes()).await;
        }
        Ok(())
    }

    async fn write_ndef(&mut self, rf: &mut Rf, args: &[&str]) -> Result<(), NfcError> {
        let (mut text, mut uri, mut lang) = (None, None, "en");
        let mut i = 0;
        while i < args.len() {
            let Some(&v) = args.get(i + 1) else {
                self.print(format_args!("bad option: {}\r\n", args[i])).await;
                return Err(NfcError::Param);
            };
            match args[i] {
                "--text" => text = Some(v),
                "--lang" => lang = v,
                "--uri" => uri = Some(v),
                other => {
                    self.print(format_args!("bad option: {}\r\n", other)).await;
                    return Err(NfcError::Param);
                }
            }
            i += 2;
        }
        if text.is_none() && uri.is_none() {
            self.print(format_args!("give --text and/or --uri\r\n")).await;
            return Err(NfcError::Param);
        }
        let mut recs: heapless::Vec<ndef::Record, 2> = heapless::Vec::new();
        if let Some(t) = text {
            let _ = recs.push(ndef::text_record(t, lang)?);
        }
        if let Some(u) = uri {
            let _ = recs.push(ndef::uri_record(u)?);
        }
        let n = nfc::open(rf).await?.write_ndef(&recs).await?;
        self.print(format_args!("wrote {} page(s)\r\n", n)).await;
        Ok(())
    }

    async fn write_page(&mut self, rf: &mut Rf, args: &[&str]) -> Result<(), NfcError> {
        let force = args.contains(&"--force");
        let mut pos = args.iter().filter(|a| **a != "--force");
        let (page, hex) = (pos.next(), pos.next());
        let parsed = match (page, hex, pos.next()) {
            (Some(p), Some(h), None) => p.parse::<u8>().ok().zip(parse_hex4(h)),
            _ => None,
        };
        let Some((page, data)) = parsed else {
            self.print(format_args!("usage: write-page <page> <8 hex digits> [--force]\r\n")).await;
            return Err(NfcError::Param);
        };
        nfc::open(rf).await?.write_page(page, data, force, true).await?;
        self.print(format_args!(
            "page {} = {:02X}{:02X}{:02X}{:02X} (verified where readable)\r\n",
            page, data[0], data[1], data[2], data[3]
        ))
        .await;
        Ok(())
    }

    /// ISO-DEP plus DESFire GET_VERSION and GET_APPLICATION_IDS: what the card reveals without keys.
    async fn desfire(&mut self, rf: &mut Rf) -> Result<(), NfcError> {
        let i = nfc::desfire_info(rf).await?;
        let mut hex = |s: &mut String<96>, b: &[u8]| {
            for (n, x) in b.iter().enumerate() {
                let _ = write!(s, "{}{:02X}", if n > 0 { " " } else { "" }, x);
            }
        };
        let mut s: String<96> = String::new();
        hex(&mut s, &i.uid);
        self.print(format_args!("UID:  {}\r\nATQA: {:02X}{:02X}  SAK: 0x{:02X}\r\n", s, i.atqa[0], i.atqa[1], i.sak)).await;
        s.clear();
        hex(&mut s, &i.ats);
        self.print(format_args!("ATS:  {}\r\n", s)).await;
        let v = &i.version;
        if v.len() >= 28 {
            let storage = if v[5] & 1 == 0 { 1u32 << (v[5] >> 1) } else { 0 };
            self.print(format_args!(
                "Version: HW {}.{} SW {}.{} vendor 0x{:02X} type 0x{:02X} storage {} bytes\r\n",
                v[3], v[4], v[10], v[11], v[0], v[1], storage
            ))
            .await;
            s.clear();
            hex(&mut s, &v[14..21]);
            self.print(format_args!("Card UID: {}  production week {:02X} year {:02X}\r\n", s, v[26], v[27])).await;
        }
        self.print(format_args!("GET_VERSION status: {:02X} {:02X}\r\n", i.version_sw.0, i.version_sw.1)).await;
        self.print(format_args!("GET_APPLICATION_IDS status: {:02X} {:02X}\r\n", i.app_sw.0, i.app_sw.1)).await;
        for a in i.app_ids.chunks(3) {
            s.clear();
            hex(&mut s, a);
            self.print(format_args!("  AID {}\r\n", s)).await;
        }
        Ok(())
    }

    async fn cycles(&mut self, rf: &mut Rf, arg: Option<&str>) -> Result<(), NfcError> {
        let n: u32 = match arg {
            Some(a) => a.parse().map_err(|_| NfcError::Param)?,
            None => 50,
        };
        if !(1..=1000).contains(&n) {
            return Err(NfcError::Param);
        }
        let mut first = [0u8; PAGE_COUNT * 4];
        let mut cur = [0u8; PAGE_COUNT * 4];
        let (mut ok, mut mism, mut errs) = (0u32, 0u32, 0u32);
        let mut last_err = None;
        let (mut total, mut worst) = (0u64, 0u64);
        let mut have_first = false;
        for _ in 0..n {
            let t0 = Instant::now();
            let r = async {
                let mut s = nfc::open(rf).await?;
                Self::read_all(&mut s, &mut cur).await
            }
            .await;
            if let Err(e) = r {
                errs += 1;
                last_err = Some(e);
                continue;
            }
            if !have_first {
                first = cur;
                have_first = true;
            } else if first != cur {
                mism += 1;
                continue;
            }
            ok += 1;
            let dt = t0.elapsed().as_millis();
            total += dt;
            worst = worst.max(dt);
        }
        self.print(format_args!("{}/{} cycles OK", ok, n)).await;
        if ok > 0 {
            self.print(format_args!("; per cycle avg {} ms, max {} ms", total / ok as u64, worst)).await;
        }
        self.print(format_args!("\r\n")).await;
        if mism > 0 {
            self.print(format_args!("  {} dump mismatch(es)\r\n", mism)).await;
        }
        if let Some(e) = last_err {
            self.print(format_args!("  {} error(s), last: {}\r\n", errs, e.text())).await;
        }
        if ok == n { Ok(()) } else { Err(NfcError::BadResponse) }
    }
}
