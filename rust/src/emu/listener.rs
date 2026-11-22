//! NFC-A passive target (card emulation) on the ST25R3916, driving a `Target`.
//!
//! Ported from `../arduino/src/Listener.cpp` (itself from M5Stack's MIT-licensed M5Unit-NFC
//! emulation layer). The chip answers REQA/WUPA and the whole anticollision by itself from the PT
//! memory (UID, ATQA, SAK); this code follows the target state, reads the frames that arrive once a
//! reader has selected us and answers them from the target model.
//!
//! `rnfc-st25r39` has no target mode, so this talks to the chip through the small `Chip` trait (the
//! register, FIFO and command access of rnfc's `Interface`). Register numbers are the unified ones
//! of that trait: space A is 0x00..0x3F, space B is 0x40 + its own address.
//!
//! Timing: a reader gives a tag about 5 ms to answer, so `poll` has to be called back to back and
//! nothing may print while it runs. Events go into a ring buffer and are printed afterwards.
use core::fmt::Write;

use super::{Target, TargetReply};

/// Chip access: the same operations as rnfc's `Interface`, without its error type. Errors set a flag
/// that `take_io_error` returns.
pub trait Chip {
    fn command(&mut self, cmd: u8);
    fn read_reg(&mut self, reg: u8) -> u8;
    fn read_regs(&mut self, reg: u8, out: &mut [u8]) {
        for (i, b) in out.iter_mut().enumerate() {
            *b = self.read_reg(reg + i as u8);
        }
    }
    fn write_reg(&mut self, reg: u8, val: u8);
    fn read_fifo(&mut self, out: &mut [u8]);
    fn write_fifo(&mut self, data: &[u8]);
    /// Loads the passive target memory A: UID(10) ATQA(2) SAK(3).
    fn write_pt_memory_a(&mut self, data: &[u8]);
    /// Microseconds, wrapping.
    fn now_us(&mut self) -> u32;
    fn take_io_error(&mut self) -> bool;
}

const REG_OP_CTRL: u8 = 0x02;
const REG_MODE: u8 = 0x03;
const REG_BIT_RATE: u8 = 0x04;
const REG_ISO14443A: u8 = 0x05;
const REG_PT_DEF: u8 = 0x08;
const REG_AUX: u8 = 0x0A;
const REG_MRT: u8 = 0x0F;
const REG_TIMER_EMV: u8 = 0x12;
const REG_IRQ_MASK: u8 = 0x16; // 0x16..0x19
const REG_IRQ_MAIN: u8 = 0x1A; // 0x1A..0x1D
const REG_PT_MASK: u8 = 0x19;
const REG_FIFO_STATUS1: u8 = 0x1E;
const REG_PT_DISPLAY: u8 = 0x21;
const REG_NUM_TX1: u8 = 0x22;
const REG_NUM_TX2: u8 = 0x23;
const REG_BITRATE_DETECT: u8 = 0x24;
const REG_ANT_TUNE1: u8 = 0x26;
const REG_ANT_TUNE2: u8 = 0x27;
const REG_AUX_DISPLAY: u8 = 0x31;
const REG_AUX_MOD: u8 = 0x40 + 0x28; // space B
const REG_OVERSHOOT_FIRST: u8 = 0x40 + 0x30; // space B, 4 registers

const CMD_STOP: u8 = 0xC2;
const CMD_TX_WITH_CRC: u8 = 0xC4;
const CMD_TX_WITHOUT_CRC: u8 = 0xC5;
const CMD_GO_TO_SENSE: u8 = 0xCD;
const CMD_GO_TO_SLEEP: u8 = 0xCE;
const CMD_UNMASK_RX: u8 = 0xD1;
const CMD_CLEAR_FIFO: u8 = 0xDB;

const OP_CTRL_EN: u8 = 0x80;
const OP_CTRL_RX_EN: u8 = 0x40;
const OP_CTRL_TX_EN: u8 = 0x08;
const OP_CTRL_WU: u8 = 0x04;
const PT_D_AC_AP2P: u8 = 0x08;
const PT_D_212_424_1R: u8 = 0x04;
const PT_D_106_AC_A: u8 = 0x01;
const ISO_NFC_F0: u8 = 0x20;
const ISO_NO_TX_PAR: u8 = 0x80;
const ISO_NO_RX_PAR: u8 = 0x40;
const AUX_NO_CRC_RX: u8 = 0x80;
const AUX_EFD_O: u8 = 0x40;
const AUX_OSC_OK: u8 = 0x10;

// IRQ bits in the layout main | timer << 8 | error << 16 | passive target << 24
const I_OSC: u32 = 1 << 7;
const I_RXS: u32 = 1 << 5;
const I_RXE: u32 = 1 << 4;
const I_NFCT: u32 = 1 << 8;
const I_EON: u32 = 1 << (8 + 4);
const I_EOF: u32 = 1 << (8 + 3);
const I_CRC: u32 = 1 << (16 + 7);
const I_PAR: u32 = 1 << (16 + 6);
const I_ERR2: u32 = 1 << (16 + 5);
const I_ERR1: u32 = 1 << (16 + 4);
const I_RXE_PTA: u32 = 1 << (24 + 4);
const I_WU_AX: u32 = 1 << (24 + 1);
const I_WU_A: u32 = 1 << 24;
const I_ERRORS: u32 = I_CRC | I_PAR | I_ERR1 | I_ERR2;
const DEFAULT_IRQ: u32 = I_NFCT | I_RXS | I_EON | I_EOF | I_CRC | I_ERR1 | I_ERR2 | I_PAR;
const MODE_IRQ: u32 = I_WU_A | I_WU_AX | I_RXE_PTA;
const PTA_IDLE: u8 = 0x01;
const PTA_HALT: u8 = 0x09;

pub const LOG_SIZE: usize = 256;
const FRAME_MAX: usize = 272; // up to a 256-byte ISO-DEP frame plus CRC

#[derive(Clone, Copy, PartialEq, Eq)]
enum State {
    Off,
    Idle,
    Ready,
    Active,
    Halt,
}

const STATE_NAMES: [&str; 5] = ["OFF", "IDLE", "READY", "ACTIVE", "HALT"];

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    State,
    Field,
    Rate,
    Rx,
    Tx,
    Err,
    Irq,
}

#[derive(Clone, Copy)]
struct Event {
    us: u32,
    kind: Kind,
    len: u8,
    data: [u8; 12],
}

pub struct Listener {
    state: State,
    wakeup: bool,
    bitrate: i8, // 0 = 106 kbit/s, -1 unknown
    pending: u32,
    fields: u32,
    frames: u32,
    log: [Event; LOG_SIZE],
    log_head: usize,
    log_count: usize,
    log_dropped: usize,
    raw: [u8; FRAME_MAX],
    reply: [u8; FRAME_MAX],
}

impl Default for Listener {
    fn default() -> Self {
        Self::new()
    }
}

impl Listener {
    pub fn new() -> Self {
        Self {
            state: State::Off,
            wakeup: false,
            bitrate: -1,
            pending: 0,
            fields: 0,
            frames: 0,
            log: [Event { us: 0, kind: Kind::State, len: 0, data: [0; 12] }; LOG_SIZE],
            log_head: 0,
            log_count: 0,
            log_dropped: 0,
            raw: [0; FRAME_MAX],
            reply: [0; FRAME_MAX],
        }
    }

    pub fn field_count(&self) -> u32 {
        self.fields
    }

    pub fn frame_count(&self) -> u32 {
        self.frames
    }

    // ---- log --------------------------------------------------------------------------------

    fn add_log<C: Chip>(&mut self, c: &mut C, kind: Kind, data: &[u8]) {
        let us = c.now_us();
        let idx = (self.log_head + self.log_count) % LOG_SIZE;
        if self.log_count == LOG_SIZE {
            // full: drop the oldest
            self.log_head = (self.log_head + 1) % LOG_SIZE;
            self.log_dropped += 1;
        } else {
            self.log_count += 1;
        }
        let e = &mut self.log[idx];
        e.us = us;
        e.kind = kind;
        let n = data.len().min(e.data.len());
        e.data[..n].copy_from_slice(&data[..n]);
        e.len = n as u8;
    }

    fn clear_log(&mut self) {
        self.log_head = 0;
        self.log_count = 0;
        self.log_dropped = 0;
    }

    /// Number of events in the log.
    pub fn log_len(&self) -> usize {
        self.log_count
    }

    /// Events dropped because the ring was full.
    pub fn log_dropped(&self) -> usize {
        self.log_dropped
    }

    /// Writes event `i` (0 = oldest) as one text line without the line ending.
    pub fn log_line<W: Write>(&self, i: usize, w: &mut W) {
        let t0 = if self.log_count > 0 { self.log[self.log_head].us } else { 0 };
        let e = &self.log[(self.log_head + i) % LOG_SIZE];
        let d = e.us.wrapping_sub(t0);
        let _ = write!(w, "{:6}.{:03} ms  ", d / 1000, d % 1000);
        let _ = match e.kind {
            Kind::State => write!(w, "-> {}", STATE_NAMES[e.data[0] as usize]),
            Kind::Field => write!(w, "reader field detected"),
            Kind::Rate => write!(w, "bit rate detected: 106 kbit/s"),
            Kind::Rx => write!(w, "rx  "),
            Kind::Tx => write!(w, "tx  "),
            Kind::Err => write!(w, "rx error  "),
            Kind::Irq => write!(w, "irq "),
        };
        if matches!(e.kind, Kind::Rx | Kind::Tx | Kind::Err | Kind::Irq) {
            for j in 0..e.len as usize {
                let _ = write!(w, "{:02X} ", e.data[j]);
            }
            if e.len as usize == e.data.len() {
                let _ = write!(w, "...");
            }
        }
    }

    // ---- helpers ----------------------------------------------------------------------------

    fn change<C: Chip>(c: &mut C, reg: u8, value: u8, mask: u8) {
        let v = c.read_reg(reg);
        c.write_reg(reg, (v & !mask) | (value & mask));
    }

    fn set_bits<C: Chip>(c: &mut C, reg: u8, mask: u8) {
        let v = c.read_reg(reg);
        c.write_reg(reg, v | mask);
    }

    fn clear_bits<C: Chip>(c: &mut C, reg: u8, mask: u8) {
        let v = c.read_reg(reg);
        c.write_reg(reg, v & !mask);
    }

    /// Interrupt mask registers: a 1 masks the interrupt.
    fn enable_irqs<C: Chip>(c: &mut C, bits: u32) {
        for i in 0..4u8 {
            let m = (bits >> (8 * i)) as u8;
            if m != 0 {
                let v = c.read_reg(REG_IRQ_MASK + i);
                c.write_reg(REG_IRQ_MASK + i, v & !m);
            }
        }
    }

    fn disable_irqs<C: Chip>(c: &mut C, bits: u32) {
        for i in 0..4u8 {
            let m = (bits >> (8 * i)) as u8;
            if m != 0 {
                let v = c.read_reg(REG_IRQ_MASK + i);
                c.write_reg(REG_IRQ_MASK + i, v | m);
            }
        }
    }

    fn write_mask<C: Chip>(c: &mut C, v: u32) {
        for i in 0..4u8 {
            c.write_reg(REG_IRQ_MASK + i, (v >> (24 - 8 * i)) as u8);
        }
    }

    fn read_irqs<C: Chip>(c: &mut C) -> u32 {
        let mut b = [0u8; 4];
        c.read_regs(REG_IRQ_MAIN, &mut b);
        u32::from_le_bytes(b)
    }

    /// Reads (and clears) the chip's interrupt registers into `pending`, returns the asked-for bits.
    fn irq<C: Chip>(&mut self, c: &mut C, bits: u32) -> u32 {
        self.pending |= Self::read_irqs(c);
        let got = self.pending & bits;
        self.pending &= !got;
        got
    }

    fn fifo_count<C: Chip>(c: &mut C) -> usize {
        let mut s = [0u8; 2];
        c.read_regs(REG_FIFO_STATUS1, &mut s);
        s[0] as usize | ((s[1] & 0xC0) as usize) << 2
    }

    fn read_frame<C: Chip>(&mut self, c: &mut C, max: usize) -> usize {
        let n = Self::fifo_count(c).min(max);
        if n > 0 {
            c.read_fifo(&mut self.raw[..n]);
        }
        n
    }

    // ---- set-up (configure_emulation_a + start_emulation) -----------------------------------

    /// Puts the chip in target mode with the given 7-byte UID. The chip must be in a state with the
    /// oscillator running (for example after rnfc's `Raw::field_on`). Returns false on an I2C error.
    pub fn start<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T, uid: &[u8; 7], atqa: u16, sak: u8) -> bool {
        c.command(CMD_STOP);
        Self::clear_bits(c, REG_OP_CTRL, OP_CTRL_TX_EN); // field off
        c.write_reg(REG_MODE, 0xC8); // target, NFC-A, bit rate detection
        c.write_reg(REG_PT_DEF, 0x5C); // auto response for NFC-A only
        c.write_reg(REG_PT_MASK, 0x02); // mask I_wu_ax
        c.write_reg(REG_TIMER_EMV, 0x08); // MRT step 512/fc

        // PT memory A: UID(10, padded) ATQA(2, LE) SAK of level 1..3
        let mut pt = [0u8; 15];
        pt[..7].copy_from_slice(uid);
        pt[10] = atqa as u8;
        pt[11] = (atqa >> 8) as u8;
        pt[12] = sak | 0x04; // UID not complete after level 1 (7-byte UID)
        pt[13] = sak & !0x04;
        pt[14] = sak & !0x04;
        Self::change(c, REG_AUX, 0x10, 0x30); // 7-byte UID
        c.write_pt_memory_a(&pt);

        Self::change(c, REG_PT_DEF, PT_D_AC_AP2P | PT_D_212_424_1R, PT_D_AC_AP2P | PT_D_212_424_1R | PT_D_106_AC_A);
        Self::change(c, REG_TIMER_EMV, 0x00, 0xE0);
        Self::set_bits(c, REG_TIMER_EMV, 0x08);
        c.write_reg(REG_MRT, 3); // about 100 us at 512/fc steps
        Self::clear_bits(c, REG_ISO14443A, ISO_NO_TX_PAR | ISO_NO_RX_PAR | ISO_NFC_F0);
        c.write_reg(REG_ANT_TUNE1, 0x00);
        c.write_reg(REG_ANT_TUNE2, 0xFF);
        for i in 0..4 {
            c.write_reg(REG_OVERSHOOT_FIRST + i, 0x00); // over/undershoot off
        }
        // ST's RFAL listen set-up: answer with the chip's internal load modulation (AUX_MOD in
        // register space B: lm_dri on, lm_ext off). The reader-mode init does not touch it.
        let aux_mod = c.read_reg(REG_AUX_MOD);
        c.write_reg(REG_AUX_MOD, (aux_mod & !0x20) | 0x10);
        let after = c.read_reg(REG_AUX_MOD);
        c.command(CMD_UNMASK_RX);
        self.clear_log();
        self.fields = 0;
        self.frames = 0;
        self.add_log(c, Kind::Irq, &[aux_mod, after]); // AUX_MOD before and after the change above
        self.goto_off(c, target);
        !c.take_io_error()
    }

    // ---- state transitions ------------------------------------------------------------------

    fn log_state<C: Chip>(&mut self, c: &mut C, s: State) {
        self.add_log(c, Kind::State, &[s as u8]);
    }

    fn goto_off<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        self.log_state(c, State::Off);
        self.state = State::Off;
        target.reset();
        self.wakeup = false;
        self.bitrate = -1;
        c.command(CMD_STOP);
        Self::set_bits(c, REG_OP_CTRL, OP_CTRL_RX_EN);
        Self::clear_bits(c, REG_PT_DEF, PT_D_106_AC_A); // enable the automatic response for NFC-A
        c.command(CMD_GO_TO_SENSE);
        Self::clear_bits(c, REG_ISO14443A, ISO_NFC_F0);
        Self::write_mask(c, 0xFFFF_FFFF);
        Self::read_irqs(c);
        self.pending = 0;
        Self::enable_irqs(c, I_OSC | DEFAULT_IRQ | MODE_IRQ);
        Self::set_bits(c, REG_AUX, AUX_NO_CRC_RX);
        Self::change(c, REG_MODE, 0xC8, 0x78); // bit rate detection mode
        if c.read_reg(REG_AUX_DISPLAY) & AUX_EFD_O != 0 {
            self.goto_idle(c, target);
        } else {
            Self::clear_bits(c, REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN | OP_CTRL_EN);
        }
    }

    fn goto_idle<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        self.log_state(c, State::Idle);
        let was_active = self.state == State::Active;
        self.state = State::Idle;
        if c.read_reg(REG_OP_CTRL) & OP_CTRL_EN == 0 {
            Self::set_bits(c, REG_OP_CTRL, OP_CTRL_EN | OP_CTRL_RX_EN);
            if c.read_reg(REG_AUX_DISPLAY) & AUX_OSC_OK == 0 {
                let t0 = c.now_us();
                let mut ok = false;
                while c.now_us().wrapping_sub(t0) < 1_000_000 {
                    if self.irq(c, I_OSC) != 0 {
                        ok = true;
                        break;
                    }
                }
                if !ok {
                    return self.goto_off(c, target);
                }
            }
        } else {
            self.irq(c, I_OSC);
        }
        Self::set_bits(c, REG_AUX, AUX_NO_CRC_RX);
        if was_active && !self.wakeup {
            Self::clear_bits(c, REG_PT_DEF, PT_D_106_AC_A);
            c.command(CMD_GO_TO_SENSE);
        }
        c.command(CMD_CLEAR_FIFO);
        c.command(CMD_UNMASK_RX);
        self.wakeup = false;
    }

    fn goto_ready<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        self.log_state(c, State::Ready);
        self.state = State::Ready;
        if self.irq(c, I_EOF) != 0 {
            return self.goto_off(c, target);
        }
        Self::clear_bits(c, REG_AUX, AUX_NO_CRC_RX);
        c.write_reg(REG_BIT_RATE, 0x00); // 106 kbit/s both ways
        Self::clear_bits(c, REG_OP_CTRL, OP_CTRL_WU);
        c.write_reg(REG_MODE, 0x88); // target, NFC-A, fixed bit rate
    }

    fn goto_active<C: Chip>(&mut self, c: &mut C) {
        self.log_state(c, State::Active);
        self.state = State::Active;
        Self::set_bits(c, REG_PT_DEF, PT_D_106_AC_A); // stop the automatic response: we answer from now on
        self.irq(c, I_PAR | I_CRC | I_ERR2 | I_ERR1);
        Self::enable_irqs(c, I_RXE);
    }

    fn goto_halt<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        self.log_state(c, State::Halt);
        self.state = State::Halt;
        Self::clear_bits(c, REG_PT_DEF, PT_D_106_AC_A);
        c.command(CMD_GO_TO_SLEEP);
        Self::change(c, REG_MODE, 0xC8, 0xFB);
        Self::clear_bits(c, REG_ISO14443A, ISO_NFC_F0);
        c.command(CMD_UNMASK_RX);
        Self::enable_irqs(c, I_NFCT | I_RXS | I_CRC | I_ERR1 | I_ERR2 | I_PAR | I_EON | I_EOF | MODE_IRQ);
        if c.read_reg(REG_AUX_DISPLAY) & AUX_EFD_O == 0 {
            self.goto_off(c, target);
        }
    }

    // ---- polling ----------------------------------------------------------------------------

    /// One step of the state machine; call it back to back.
    pub fn poll<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        match self.state {
            State::Off => self.poll_off(c, target),
            State::Idle => self.poll_idle(c, target),
            State::Ready => self.poll_ready(c, target),
            State::Active => self.poll_active(c, target),
            State::Halt => self.poll_halt(c, target),
        }
    }

    fn read_bitrate<C: Chip>(&mut self, c: &mut C) {
        let br = (c.read_reg(REG_BITRATE_DETECT) >> 4) & 0x03;
        self.bitrate = br.min(2) as i8;
        self.add_log(c, Kind::Rate, &[]);
    }

    fn poll_off<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        if self.irq(c, I_EON) != 0 {
            self.fields += 1;
            self.add_log(c, Kind::Field, &[]);
            self.goto_idle(c, target);
        }
    }

    fn poll_idle<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        let got = self.irq(c, I_NFCT | I_RXE | I_EOF | I_RXE_PTA);
        if got == 0 {
            return;
        }
        if got & I_NFCT != 0 {
            self.read_bitrate(c);
        }
        if got & I_EOF != 0 {
            return self.goto_off(c, target);
        }
        if got & I_RXE != 0 && self.bitrate >= 0 {
            self.irq(c, I_RXE | I_EOF | I_CRC | I_PAR | I_ERR2 | I_ERR1);
            let n = self.read_frame(c, 32);
            let raw = self.raw;
            self.add_log(c, Kind::Rx, &raw[..n]); // a frame the chip did not answer by itself
            c.command(CMD_CLEAR_FIFO);
            c.command(CMD_UNMASK_RX);
            return;
        }
        if got & I_RXE_PTA != 0 && self.bitrate == 0 {
            let pta = c.read_reg(REG_PT_DISPLAY);
            if pta & 0x0F > PTA_IDLE {
                self.goto_ready(c, target);
            }
        }
    }

    fn poll_ready<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        let got = self.irq(c, I_EOF | if self.wakeup { I_WU_AX } else { I_WU_A });
        if got == 0 {
            return;
        }
        if got & I_EOF != 0 {
            return self.goto_off(c, target);
        }
        self.goto_active(c);
    }

    fn poll_active<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        let mut got = self.irq(c, I_EOF | I_RXE);
        if got == 0 {
            return;
        }
        if got & I_EOF != 0 {
            return self.goto_off(c, target);
        }
        got |= self.irq(c, I_PAR | I_CRC | I_ERR2 | I_ERR1);
        let mut n = self.read_frame(c, FRAME_MAX);
        if got & I_ERRORS != 0 || n <= 2 {
            let raw = self.raw;
            self.add_log(c, Kind::Err, &raw[..n]);
            c.command(CMD_CLEAR_FIFO);
            c.command(CMD_UNMASK_RX);
            if self.wakeup {
                self.goto_halt(c, target);
            } else {
                self.goto_idle(c, target);
            }
            return;
        }
        n -= 2; // CRC_A stays in the FIFO
        self.frames += 1;
        let r = target.handle(&self.raw[..n], &mut self.reply);
        if r.len != 0 || r.ack4 {
            self.transmit(c, &r); // answer first, log afterwards
        }
        let raw = self.raw;
        self.add_log(c, Kind::Rx, &raw[..n]);
        if r.halt {
            Self::disable_irqs(c, I_RXE);
            self.goto_halt(c, target);
        }
    }

    fn transmit<C: Chip>(&mut self, c: &mut C, r: &TargetReply) {
        c.command(CMD_CLEAR_FIFO);
        if r.ack4 {
            // 4-bit ACK/NAK frame, no CRC
            c.write_fifo(&[r.nibble]);
            c.write_reg(REG_NUM_TX1, 0x00);
            c.write_reg(REG_NUM_TX2, 4); // 0 bytes, 4 bits
            c.command(CMD_TX_WITHOUT_CRC);
            self.add_log(c, Kind::Tx, &[r.nibble]);
            return;
        }
        c.write_fifo(&self.reply[..r.len]);
        c.write_reg(REG_NUM_TX1, ((r.len << 3) >> 8) as u8);
        c.write_reg(REG_NUM_TX2, (r.len << 3) as u8);
        c.command(CMD_TX_WITH_CRC);
        let n = r.len.min(12);
        let mut head = [0u8; 12];
        head[..n].copy_from_slice(&self.reply[..n]);
        self.add_log(c, Kind::Tx, &head[..n]);
    }

    fn poll_halt<C: Chip, T: Target>(&mut self, c: &mut C, target: &mut T) {
        let got = self.irq(c, I_NFCT | I_RXE | I_EOF | I_RXE_PTA);
        if got == 0 {
            return;
        }
        if got & I_NFCT != 0 {
            self.read_bitrate(c);
        }
        if got & I_EOF != 0 {
            return self.goto_off(c, target);
        }
        if got & I_RXE != 0 && self.bitrate >= 0 {
            c.command(CMD_CLEAR_FIFO);
            c.command(CMD_UNMASK_RX);
            return;
        }
        if got & I_RXE_PTA != 0 && self.bitrate == 0 {
            let pta = c.read_reg(REG_PT_DISPLAY);
            if pta & 0x0F > PTA_HALT {
                self.wakeup = true;
                self.goto_ready(c, target);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::emu::type2tag::Type2Tag;
    use std::collections::VecDeque;

    /// A scripted chip: registers are a plain array, the interrupt registers return the next queued
    /// value, the FIFO holds what the test puts in it, and everything written is recorded.
    struct Fake {
        regs: [u8; 0x80],
        irqs: VecDeque<u32>,
        fifo_in: Vec<u8>,
        fifo_out: Vec<u8>,
        pt_mem: Vec<u8>,
        commands: Vec<u8>,
        us: u32,
    }

    impl Fake {
        fn new() -> Self {
            let mut regs = [0u8; 0x80];
            regs[REG_AUX_DISPLAY as usize] = AUX_EFD_O | AUX_OSC_OK; // reader field present, oscillator up
            Fake { regs, irqs: VecDeque::new(), fifo_in: vec![], fifo_out: vec![], pt_mem: vec![], commands: vec![], us: 0 }
        }
    }

    impl Chip for Fake {
        fn command(&mut self, cmd: u8) {
            self.commands.push(cmd);
        }
        fn read_reg(&mut self, reg: u8) -> u8 {
            self.regs[reg as usize]
        }
        fn read_regs(&mut self, reg: u8, out: &mut [u8]) {
            if reg == REG_IRQ_MAIN {
                out.copy_from_slice(&self.irqs.pop_front().unwrap_or(0).to_le_bytes());
            } else if reg == REG_FIFO_STATUS1 {
                let n = self.fifo_in.len();
                out[0] = n as u8;
                out[1] = ((n >> 8) as u8) << 6;
            } else {
                for (i, b) in out.iter_mut().enumerate() {
                    *b = self.regs[reg as usize + i];
                }
            }
        }
        fn write_reg(&mut self, reg: u8, val: u8) {
            self.regs[reg as usize] = val;
        }
        fn read_fifo(&mut self, out: &mut [u8]) {
            out.copy_from_slice(&self.fifo_in[..out.len()]);
            self.fifo_in.drain(..out.len());
        }
        fn write_fifo(&mut self, data: &[u8]) {
            self.fifo_out = data.to_vec();
        }
        fn write_pt_memory_a(&mut self, data: &[u8]) {
            self.pt_mem = data.to_vec();
        }
        fn now_us(&mut self) -> u32 {
            self.us += 10;
            self.us
        }
        fn take_io_error(&mut self) -> bool {
            false
        }
    }

    fn lines(l: &Listener) -> Vec<String> {
        (0..l.log_len())
            .map(|i| {
                let mut s = String::new();
                l.log_line(i, &mut s);
                s
            })
            .collect()
    }

    #[test]
    fn selected_by_a_reader_and_answers_a_read() {
        let uid = [0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66];
        let mut chip = Fake::new();
        let mut tag = Type2Tag::new(uid);
        let mut l = Listener::new();

        assert!(l.start(&mut chip, &mut tag, &uid, 0x0044, 0x00));
        // PT memory A: UID padded to 10 bytes, ATQA little endian, SAK of the three cascade levels
        assert_eq!(chip.pt_mem, [0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0, 0, 0, 0x44, 0x00, 0x04, 0x00, 0x00]);
        assert_eq!(chip.regs[REG_MODE as usize], 0xC8); // target mode with bit rate detection
        // the reader field is already there: the start-up ended in IDLE
        assert!(lines(&l).last().unwrap().ends_with("-> IDLE"));

        // the reader's REQA is answered by the chip: bit rate detected, then the PT state machine
        // reports the select (SAK sent), then the reader's next frame arrives in the FIFO
        chip.regs[REG_BITRATE_DETECT as usize] = 0x00; // 106 kbit/s
        chip.irqs.push_back(I_NFCT);
        l.poll(&mut chip, &mut tag);
        chip.regs[REG_PT_DISPLAY as usize] = 0x02; // beyond idle: the chip answered the anticollision
        chip.irqs.push_back(I_RXE_PTA);
        l.poll(&mut chip, &mut tag);
        chip.irqs.push_back(I_WU_A);
        l.poll(&mut chip, &mut tag);
        assert!(lines(&l).last().unwrap().ends_with("-> ACTIVE"));

        // READ page 0 (30 00 + CRC_A that the chip leaves in the FIFO)
        chip.fifo_in = vec![0x30, 0x00, 0x02, 0xA8];
        chip.irqs.push_back(I_RXE);
        chip.commands.clear();
        l.poll(&mut chip, &mut tag);
        assert_eq!(l.frame_count(), 1);
        assert_eq!(chip.fifo_out.len(), 16);
        assert_eq!(&chip.fifo_out[..4], &[0x04, 0x11, 0x22, 0x88 ^ 0x04 ^ 0x11 ^ 0x22]);
        assert_eq!((chip.regs[REG_NUM_TX1 as usize], chip.regs[REG_NUM_TX2 as usize]), (0x00, 128)); // 16 bytes = 128 bits
        assert_eq!(*chip.commands.last().unwrap(), CMD_TX_WITH_CRC);

        // a WRITE is acknowledged with a 4-bit frame without CRC
        chip.fifo_in = vec![0xA2, 0x04, 0x03, 0x0A, 0xD1, 0x01, 0x00, 0x00];
        chip.irqs.push_back(I_RXE);
        l.poll(&mut chip, &mut tag);
        assert_eq!(chip.fifo_out, [0x0A]);
        assert_eq!(chip.regs[REG_NUM_TX2 as usize], 4);
        assert_eq!(*chip.commands.last().unwrap(), CMD_TX_WITHOUT_CRC);
        assert_eq!(tag.write_count(), 1);

        // HLTA: no reply, the listener halts
        chip.fifo_in = vec![0x50, 0x00, 0x00, 0x00];
        chip.irqs.push_back(I_RXE);
        l.poll(&mut chip, &mut tag);
        assert!(lines(&l).iter().any(|s| s.ends_with("-> HALT")));

        // the reader switches its field off: back to OFF
        chip.irqs.push_back(I_EOF);
        l.poll(&mut chip, &mut tag);
        // start-up logged one OFF; the field going away logs a second (then IDLE again, the fake keeps a field)
        assert_eq!(lines(&l).iter().filter(|s| s.ends_with("-> OFF")).count(), 2);
    }

    #[test]
    fn a_frame_with_a_bad_crc_is_dropped() {
        let uid = [0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66];
        let mut chip = Fake::new();
        let mut tag = Type2Tag::new(uid);
        let mut l = Listener::new();
        assert!(l.start(&mut chip, &mut tag, &uid, 0x0044, 0x00));
        chip.regs[REG_BITRATE_DETECT as usize] = 0;
        chip.irqs.push_back(I_NFCT);
        l.poll(&mut chip, &mut tag);
        chip.regs[REG_PT_DISPLAY as usize] = 0x02;
        chip.irqs.push_back(I_RXE_PTA);
        l.poll(&mut chip, &mut tag);
        chip.irqs.push_back(I_WU_A);
        l.poll(&mut chip, &mut tag);
        chip.fifo_in = vec![0x30, 0x00, 0x00, 0x00];
        chip.irqs.push_back(I_RXE | I_CRC);
        chip.fifo_out.clear();
        l.poll(&mut chip, &mut tag);
        assert_eq!(l.frame_count(), 0);
        assert!(chip.fifo_out.is_empty());
        assert!(lines(&l).iter().any(|s| s.contains("rx error")));
    }
}
