#include "Listener.h"

#include <string.h>

#include <zephyr/kernel.h>

namespace nfc {

namespace {
constexpr uint8_t REG_OP_CTRL = 0x02;
constexpr uint8_t REG_MODE = 0x03;
constexpr uint8_t REG_BIT_RATE = 0x04;
constexpr uint8_t REG_ISO14443A = 0x05;
constexpr uint8_t REG_PT_DEF = 0x08;
constexpr uint8_t REG_AUX = 0x0A;
constexpr uint8_t REG_MRT = 0x0F;
constexpr uint8_t REG_TIMER_EMV = 0x12;
constexpr uint8_t REG_IRQ_MASK = 0x16;  // 0x16..0x19
constexpr uint8_t REG_PT_MASK = 0x19;
constexpr uint8_t REG_PT_DISPLAY = 0x21;
constexpr uint8_t REG_NUM_TX1 = 0x22;
constexpr uint8_t REG_NUM_TX2 = 0x23;
constexpr uint8_t REG_BITRATE_DETECT = 0x24;
constexpr uint8_t REG_ANT_TUNE1 = 0x26;
constexpr uint8_t REG_ANT_TUNE2 = 0x27;
constexpr uint8_t REG_AUX_DISPLAY = 0x31;

constexpr uint8_t CMD_STOP = 0xC2;
constexpr uint8_t CMD_TX_WITH_CRC = 0xC4;
constexpr uint8_t CMD_TX_WITHOUT_CRC = 0xC5;
constexpr uint8_t CMD_GO_TO_SENSE = 0xCD;
constexpr uint8_t CMD_GO_TO_SLEEP = 0xCE;
constexpr uint8_t CMD_UNMASK_RX = 0xD1;
constexpr uint8_t CMD_CLEAR_FIFO = 0xDB;

constexpr uint8_t OP_CTRL_EN = 0x80;
constexpr uint8_t OP_CTRL_RX_EN = 0x40;
constexpr uint8_t OP_CTRL_TX_EN = 0x08;
constexpr uint8_t OP_CTRL_WU = 0x04;
constexpr uint8_t PT_D_AC_AP2P = 0x08;
constexpr uint8_t PT_D_212_424_1R = 0x04;
constexpr uint8_t PT_D_106_AC_A = 0x01;
constexpr uint8_t ISO_NFC_F0 = 0x20;
constexpr uint8_t ISO_NO_TX_PAR = 0x80;
constexpr uint8_t ISO_NO_RX_PAR = 0x40;
constexpr uint8_t AUX_NO_CRC_RX = 0x80;
constexpr uint8_t AUX_EFD_O = 0x40;
constexpr uint8_t AUX_OSC_OK = 0x10;

// IRQ bits in readIrqs() layout: main | timer << 8 | error << 16 | passive target << 24
constexpr uint32_t I_OSC = 1u << 7;
constexpr uint32_t I_RXS = 1u << 5;
constexpr uint32_t I_RXE = 1u << 4;
constexpr uint32_t I_NFCT = 1u << 8;
constexpr uint32_t I_EON = 1u << (8 + 4);
constexpr uint32_t I_EOF = 1u << (8 + 3);
constexpr uint32_t I_CRC = 1u << (16 + 7);
constexpr uint32_t I_PAR = 1u << (16 + 6);
constexpr uint32_t I_ERR2 = 1u << (16 + 5);
constexpr uint32_t I_ERR1 = 1u << (16 + 4);
constexpr uint32_t I_RXE_PTA = 1u << (24 + 4);
constexpr uint32_t I_WU_AX = 1u << (24 + 1);
constexpr uint32_t I_WU_A = 1u << 24;
constexpr uint32_t I_ERRORS = I_CRC | I_PAR | I_ERR1 | I_ERR2;
constexpr uint32_t DEFAULT_IRQ = I_NFCT | I_RXS | I_EON | I_EOF | I_CRC | I_ERR1 | I_ERR2 | I_PAR;
constexpr uint32_t MODE_IRQ = I_WU_A | I_WU_AX | I_RXE_PTA;
constexpr uint8_t PTA_IDLE = 0x01;
constexpr uint8_t PTA_HALT = 0x09;

const char* const kStateNames[] = {"OFF", "IDLE", "READY", "ACTIVE", "HALT"};
}  // namespace

// ---- log ----------------------------------------------------------------------------------
void Listener::log(Kind kind, const uint8_t* data, size_t len) {
  Event& e = log_[(logHead_ + logCount_) % LOG_SIZE];
  if (logCount_ == LOG_SIZE) {  // full: drop the oldest
    logHead_ = (logHead_ + 1) % LOG_SIZE;
    logDropped_++;
  } else {
    logCount_++;
  }
  e.us = (uint32_t)k_ticks_to_us_floor64(k_uptime_ticks());
  e.kind = kind;
  e.len = len > sizeof(e.data) ? sizeof(e.data) : len;
  if (e.len) memcpy(e.data, data, e.len);
}

void Listener::logState(State s) {
  uint8_t b = s;
  log(EV_STATE, &b, 1);
}

void Listener::printLog(Io& io) {
  uint32_t t0 = logCount_ ? log_[logHead_].us : 0;
  if (logDropped_) io.printf("(%u older events dropped)\n", (unsigned)logDropped_);
  for (size_t i = 0; i < logCount_; i++) {
    const Event& e = log_[(logHead_ + i) % LOG_SIZE];
    uint32_t d = e.us - t0;  // integer arithmetic: keep it integer: no float printf here either
    io.printf("%6u.%03u ms  ", (unsigned)(d / 1000), (unsigned)(d % 1000));
    switch (e.kind) {
      case EV_STATE: io.printf("-> %s", kStateNames[e.data[0]]); break;
      case EV_FIELD: io.print("reader field detected"); break;
      case EV_RATE: io.print("bit rate detected: 106 kbit/s"); break;
      case EV_RX: io.print("rx  "); break;
      case EV_TX: io.print("tx  "); break;
      case EV_ERR: io.print("rx error  "); break;
      case EV_IRQ: io.print("irq "); break;
    }
    if (e.kind == EV_RX || e.kind == EV_TX || e.kind == EV_ERR || e.kind == EV_IRQ) {
      for (uint8_t j = 0; j < e.len; j++) io.printf("%02X ", e.data[j]);
      if (e.len == sizeof(e.data)) io.print("...");
    }
    io.println();
  }
  logHead_ = logCount_ = logDropped_ = 0;
}

// ---- helpers ------------------------------------------------------------------------------
void Listener::change(uint8_t reg, uint8_t value, uint8_t mask) {
  rf_.writeReg(reg, (rf_.readReg(reg) & ~mask) | (value & mask));
}

// Interrupt mask registers: a 1 masks the interrupt.
void Listener::enableIrqs(uint32_t bits) {
  for (int i = 0; i < 4; i++) {
    uint8_t m = (bits >> (8 * i)) & 0xFF;
    if (m) rf_.writeReg(REG_IRQ_MASK + i, rf_.readReg(REG_IRQ_MASK + i) & ~m);
  }
}
void Listener::disableIrqs(uint32_t bits) {
  for (int i = 0; i < 4; i++) {
    uint8_t m = (bits >> (8 * i)) & 0xFF;
    if (m) rf_.writeReg(REG_IRQ_MASK + i, rf_.readReg(REG_IRQ_MASK + i) | m);
  }
}

// Reads (and clears) the chip's interrupt registers into `pending_`, returns the asked-for bits.
uint32_t Listener::irq(uint32_t bits) {
  pending_ |= rf_.readIrqs();
  uint32_t got = pending_ & bits;
  pending_ &= ~got;
  return got;
}

// ---- set-up (configure_emulation_a + start_emulation) -------------------------------------
int Listener::start(const uint8_t uid[7], uint16_t atqa, uint8_t sak) {
  int rc = rf_.init();  // reader mode, field on
  if (rc != OK) return rc;
  rf_.command(CMD_STOP);
  rf_.clearBits(REG_OP_CTRL, OP_CTRL_TX_EN);  // field off
  rf_.writeReg(REG_MODE, 0xC8);               // target, NFC-A, bit rate detection
  rf_.writeReg(REG_PT_DEF, 0x5C);             // auto response for NFC-A only
  rf_.writeReg(REG_PT_MASK, 0x02);            // mask I_wu_ax
  rf_.writeReg(REG_TIMER_EMV, 0x08);          // MRT step 512/fc

  // PT memory A: UID(10, padded) ATQA(2, LE) SAK of level 1..3
  uint8_t pt[15] = {0};
  memcpy(pt, uid, 7);
  pt[10] = atqa & 0xFF;
  pt[11] = atqa >> 8;
  pt[12] = sak | 0x04;  // UID not complete after level 1 (7-byte UID)
  pt[13] = sak & ~0x04;
  pt[14] = sak & ~0x04;
  change(REG_AUX, 0x10, 0x30);  // 7-byte UID
  rf_.writePtMemoryA(pt, sizeof(pt));

  change(REG_PT_DEF, PT_D_AC_AP2P | PT_D_212_424_1R, PT_D_AC_AP2P | PT_D_212_424_1R | PT_D_106_AC_A);
  change(REG_TIMER_EMV, 0x00, 0xE0);
  rf_.setBits(REG_TIMER_EMV, 0x08);
  rf_.writeReg(REG_MRT, 3);  // about 100 us at 512/fc steps
  rf_.clearBits(REG_ISO14443A, ISO_NO_TX_PAR | ISO_NO_RX_PAR | ISO_NFC_F0);
  // ST's RFAL listen set-up for the ST25R3916 (stm32duino/ST25R3916, src/rfal_rfst25r3916_analogConfigTbl.h,
  // "Chip-Specific Listen On"): antenna tuning A=0x00 and B=0xFF, over/undershoot protection off. The 3916B
  // table differs (0x00/0x00). M5Stack's 0x82/0x82 is the poller value from the same table, not the listen one.
  rf_.writeReg(REG_ANT_TUNE1, 0x00);
  rf_.writeReg(REG_ANT_TUNE2, 0xFF);
  for (uint8_t reg = 0x30; reg <= 0x33; reg++) rf_.writeSpaceB(reg, 0x00);  // over/undershoot off
  // ST's RFAL listen set-up (rfal_analogConfigTbl.h): answer with the chip's internal load modulation
  // (AUX_MOD in register space B: lm_dri on, lm_ext off). The reader-mode init does not touch it.
  uint8_t auxMod = rf_.readSpaceB(0x28);
  rf_.writeSpaceB(0x28, (auxMod & ~0x20) | 0x10);
  uint8_t before[2] = {auxMod, rf_.readSpaceB(0x28)};
  rf_.command(CMD_UNMASK_RX);
  logHead_ = logCount_ = logDropped_ = 0;
  fields_ = frames_ = 0;
  log(EV_IRQ, before, 2);  // AUX_MOD before and after the change above
  gotoOff();
  return rf_.takeIoError() ? ERR_I2C : OK;
}

int Listener::stop() {
  rf_.command(CMD_STOP);
  int rc = rf_.init();
  rf_.fieldOff();  // init() leaves the reader field on, which makes a phone nearby open Wallet
  return rc;
}

// ---- state transitions --------------------------------------------------------------------
void Listener::gotoOff() {
  logState(OFF);
  state_ = OFF;
  target_->reset();
  wakeup_ = false;
  bitrate_ = -1;
  rf_.command(CMD_STOP);
  rf_.setBits(REG_OP_CTRL, OP_CTRL_RX_EN);
  rf_.clearBits(REG_PT_DEF, PT_D_106_AC_A);  // enable the automatic response for NFC-A
  rf_.command(CMD_GO_TO_SENSE);
  rf_.clearBits(REG_ISO14443A, ISO_NFC_F0);
  rf_.writeMask(0xFFFFFFFF);
  rf_.readIrqs();
  pending_ = 0;
  enableIrqs(I_OSC | DEFAULT_IRQ | MODE_IRQ);
  rf_.setBits(REG_AUX, AUX_NO_CRC_RX);
  change(REG_MODE, 0xC8, 0x78);  // bit rate detection mode
  if (rf_.readReg(REG_AUX_DISPLAY) & AUX_EFD_O) {
    gotoIdle();
  } else {
    rf_.clearBits(REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN | OP_CTRL_EN);
  }
}

void Listener::gotoIdle() {
  logState(IDLE);
  bool wasActive = state_ == ACTIVE;
  state_ = IDLE;
  if (!(rf_.readReg(REG_OP_CTRL) & OP_CTRL_EN)) {
    rf_.setBits(REG_OP_CTRL, OP_CTRL_EN | OP_CTRL_RX_EN);
    if (!(rf_.readReg(REG_AUX_DISPLAY) & AUX_OSC_OK)) {
      uint32_t t0 = k_uptime_get_32();
      bool ok = false;
      while (k_uptime_get_32() - t0 < 1000) {
        if (irq(I_OSC)) {
          ok = true;
          break;
        }
      }
      if (!ok) return gotoOff();
    }
  } else {
    irq(I_OSC);
  }
  rf_.setBits(REG_AUX, AUX_NO_CRC_RX);
  if (wasActive && !wakeup_) {
    rf_.clearBits(REG_PT_DEF, PT_D_106_AC_A);
    rf_.command(CMD_GO_TO_SENSE);
  }
  rf_.command(CMD_CLEAR_FIFO);
  rf_.command(CMD_UNMASK_RX);
  wakeup_ = false;
}

void Listener::gotoReady() {
  logState(READY);
  state_ = READY;
  if (irq(I_EOF)) return gotoOff();
  rf_.clearBits(REG_AUX, AUX_NO_CRC_RX);
  rf_.writeReg(REG_BIT_RATE, 0x00);  // 106 kbit/s both ways
  rf_.clearBits(REG_OP_CTRL, OP_CTRL_WU);
  rf_.writeReg(REG_MODE, 0x88);  // target, NFC-A, fixed bit rate
}

void Listener::gotoActive() {
  logState(ACTIVE);
  state_ = ACTIVE;
  rf_.setBits(REG_PT_DEF, PT_D_106_AC_A);  // stop the automatic response: we answer from now on
  irq(I_PAR | I_CRC | I_ERR2 | I_ERR1);
  enableIrqs(I_RXE);
}

void Listener::gotoHalt() {
  logState(HALT);
  state_ = HALT;
  rf_.clearBits(REG_PT_DEF, PT_D_106_AC_A);
  rf_.command(CMD_GO_TO_SLEEP);
  change(REG_MODE, 0xC8, 0xFB);
  rf_.clearBits(REG_ISO14443A, ISO_NFC_F0);
  rf_.command(CMD_UNMASK_RX);
  enableIrqs(I_NFCT | I_RXS | I_CRC | I_ERR1 | I_ERR2 | I_PAR | I_EON | I_EOF | MODE_IRQ);
  if (!(rf_.readReg(REG_AUX_DISPLAY) & AUX_EFD_O)) gotoOff();
}

// ---- polling ------------------------------------------------------------------------------
void Listener::poll() {
  switch (state_) {
    case OFF: pollOff(); break;
    case IDLE: pollIdle(); break;
    case READY: pollReady(); break;
    case ACTIVE: pollActive(); break;
    case HALT: pollHalt(); break;
  }
}

void Listener::readBitrate() {
  uint8_t br = (rf_.readReg(REG_BITRATE_DETECT) >> 4) & 0x03;
  bitrate_ = br > 2 ? 2 : br;
  log(EV_RATE);
}

bool Listener::readFrame(uint8_t* out, size_t cap, size_t* n) {
  size_t count = rf_.fifoCount();
  if (count > cap) count = cap;
  *n = count ? rf_.readFifo(out, count) : 0;
  return true;
}

void Listener::pollOff() {
  if (irq(I_EON)) {
    fields_++;
    log(EV_FIELD);
    gotoIdle();
  }
}

void Listener::pollIdle() {
  uint32_t got = irq(I_NFCT | I_RXE | I_EOF | I_RXE_PTA);
  if (!got) return;
  if (got & I_NFCT) readBitrate();
  if (got & I_EOF) return gotoOff();
  if ((got & I_RXE) && bitrate_ >= 0) {
    irq(I_RXE | I_EOF | I_CRC | I_PAR | I_ERR2 | I_ERR1);
    uint8_t raw[32];
    size_t n;
    readFrame(raw, sizeof(raw), &n);
    log(EV_RX, raw, n);  // a frame the chip did not answer by itself
    rf_.command(CMD_CLEAR_FIFO);
    rf_.command(CMD_UNMASK_RX);
    return;
  }
  if ((got & I_RXE_PTA) && bitrate_ == 0) {
    uint8_t pta = rf_.readReg(REG_PT_DISPLAY);
    if ((pta & 0x0F) > PTA_IDLE) gotoReady();
  }
}

void Listener::pollReady() {
  uint32_t got = irq(I_EOF | (wakeup_ ? I_WU_AX : I_WU_A));
  if (!got) return;
  if (got & I_EOF) return gotoOff();
  gotoActive();
}

void Listener::pollActive() {
  uint32_t got = irq(I_EOF | I_RXE);
  if (!got) return;
  if (got & I_EOF) return gotoOff();
  got |= irq(I_PAR | I_CRC | I_ERR2 | I_ERR1);
  uint8_t raw[272];  // up to a 256-byte ISO-DEP frame
  size_t n;
  readFrame(raw, sizeof(raw), &n);
  if ((got & I_ERRORS) || n <= 2) {
    log(EV_ERR, raw, n);
    rf_.command(CMD_CLEAR_FIFO);
    rf_.command(CMD_UNMASK_RX);
    return wakeup_ ? gotoHalt() : gotoIdle();
  }
  n -= 2;  // CRC_A stays in the FIFO
  frames_++;
  uint8_t reply[272];
  TargetReply r = target_->handle(raw, n, reply, sizeof(reply));
  if (r.len || r.ack4) transmit(r, reply);  // answer first, log afterwards
  log(EV_RX, raw, n);
  if (r.halt) {
    disableIrqs(I_RXE);
    gotoHalt();
  }
}

void Listener::transmit(const TargetReply& r, const uint8_t* data) {
  rf_.command(CMD_CLEAR_FIFO);
  if (r.ack4) {  // 4-bit ACK/NAK frame, no CRC
    rf_.loadFifo(&r.nibble, 1);
    rf_.writeReg(REG_NUM_TX1, 0x00);
    rf_.writeReg(REG_NUM_TX2, 4 << 0);  // 0 bytes, 4 bits
    rf_.command(CMD_TX_WITHOUT_CRC);
    log(EV_TX, &r.nibble, 1);
    return;
  }
  rf_.loadFifo(data, r.len);
  rf_.writeReg(REG_NUM_TX1, (r.len << 3) >> 8);
  rf_.writeReg(REG_NUM_TX2, (r.len << 3) & 0xFF);
  rf_.command(CMD_TX_WITH_CRC);
  log(EV_TX, data, r.len);
}

void Listener::pollHalt() {
  uint32_t got = irq(I_NFCT | I_RXE | I_EOF | I_RXE_PTA);
  if (!got) return;
  if (got & I_NFCT) readBitrate();
  if (got & I_EOF) return gotoOff();
  if ((got & I_RXE) && bitrate_ >= 0) {
    rf_.command(CMD_CLEAR_FIFO);
    rf_.command(CMD_UNMASK_RX);
    return;
  }
  if ((got & I_RXE_PTA) && bitrate_ == 0) {
    uint8_t pta = rf_.readReg(REG_PT_DISPLAY);
    if ((pta & 0x0F) > PTA_HALT) {
      wakeup_ = true;
      gotoReady();
    }
  }
}

}  // namespace nfc
