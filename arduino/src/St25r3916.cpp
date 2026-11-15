#include "St25r3916.h"

#include <math.h>

namespace nfc {

namespace {
constexpr uint8_t OP_READ = 0x40;
constexpr uint8_t CMD_SPACE_B = 0xFB;
constexpr uint8_t OP_FIFO_LOAD = 0x80;
constexpr uint8_t OP_FIFO_READ = 0x9F;

constexpr uint8_t CMD_SET_DEFAULT = 0xC1;
constexpr uint8_t CMD_STOP = 0xC2;
constexpr uint8_t CMD_TX_WITH_CRC = 0xC4;
constexpr uint8_t CMD_TX_WITHOUT_CRC = 0xC5;
constexpr uint8_t CMD_INITIAL_FIELD_ON = 0xC8;
constexpr uint8_t CMD_RESET_RX_GAIN = 0xD5;
constexpr uint8_t CMD_ADJUST_REGULATORS = 0xD6;
constexpr uint8_t CMD_CLEAR_FIFO = 0xDB;
constexpr uint8_t CMD_TEST_ACCESS = 0xFC;

constexpr uint8_t REG_OP_CTRL = 0x02;
constexpr uint8_t REG_MODE = 0x03;
constexpr uint8_t REG_BIT_RATE = 0x04;
constexpr uint8_t REG_ISO14443A = 0x05;
constexpr uint8_t REG_AUX = 0x0A;
constexpr uint8_t REG_NRT1 = 0x10;
constexpr uint8_t REG_NRT2 = 0x11;
constexpr uint8_t REG_TIMER_EMV = 0x12;
constexpr uint8_t REG_IRQ_MASK = 0x16;  // 0x16..0x19
constexpr uint8_t REG_IRQ_MAIN = 0x1A;  // 0x1A..0x1D, read clears
constexpr uint8_t REG_FIFO_STATUS1 = 0x1E;
constexpr uint8_t REG_NUM_TX1 = 0x22;
constexpr uint8_t REG_NUM_TX2 = 0x23;
constexpr uint8_t REG_AUX_DISPLAY = 0x31;
constexpr uint8_t REG_IC_IDENTITY = 0x3F;

constexpr uint8_t OP_CTRL_EN = 0x80;
constexpr uint8_t OP_CTRL_TX_EN = 0x08;
constexpr uint8_t OP_CTRL_RX_EN = 0x40;
constexpr uint8_t AUX_NO_CRC_RX = 0x80;  // no_crc_rx bit in AUX (0x0A); position unverified. transceive() strips CRC_A itself.
constexpr uint8_t AUX_DISPLAY_OSC_OK = 0x10;

// IRQ bits, combined as main | timer<<8 | error<<16 | target<<24
constexpr uint32_t IRQ_OSC = 1u << 7;
constexpr uint32_t IRQ_RXS = 1u << 5;
constexpr uint32_t IRQ_RXE = 1u << 4;
constexpr uint32_t IRQ_COL = 1u << 2;
constexpr uint32_t IRQ_NRE = 1u << (8 + 6);
constexpr uint32_t IRQ_CRC = 1u << (16 + 7);
constexpr uint32_t IRQ_PAR = 1u << (16 + 6);
constexpr uint32_t IRQ_ERR2 = 1u << (16 + 5);
constexpr uint32_t IRQ_ERR1 = 1u << (16 + 4);
constexpr uint32_t IRQ_ERRORS = IRQ_CRC | IRQ_PAR | IRQ_ERR2 | IRQ_ERR1;

constexpr float NRT_STEP_US = 4096.0f / 13.56f;  // 4096/fc
constexpr float NRT_FINE_US = 64.0f / 13.56f;    // 64/fc

// ISO14443-A CRC, low byte first (used to strip the CRC the chip leaves in the FIFO)
uint16_t crcA(const uint8_t* data, size_t len) {
  uint16_t crc = 0x6363;
  for (size_t i = 0; i < len; i++) {
    uint8_t ch = data[i] ^ (crc & 0xFF);
    ch ^= (uint8_t)(ch << 4);
    crc = (crc >> 8) ^ ((uint16_t)ch << 8) ^ ((uint16_t)ch << 3) ^ (ch >> 4);
  }
  return crc;
}
}  // namespace

const char* statusName(int status) {
  switch (status) {
    case OK: return "ok";
    case ERR_TIMEOUT: return "no response from card";
    case ERR_COLLISION: return "collision";
    case ERR_FRAME: return "receive error";
    case ERR_I2C: return "I2C error";
    case ERR_BAD_RESPONSE: return "bad response";
    case ERR_NO_CHIP: return "no ST25R3916 on the bus";
    case ERR_OSC: return "oscillator did not start";
    case ERR_PARAM: return "bad parameter";
    case ERR_NO_ACK: return "no ACK";
    case ERR_PROTECTED: return "page is protected (read-only, out of range, or one-way lock/config)";
    case ERR_VERIFY: return "verify failed";
    case ERR_TOO_LARGE: return "message does not fit the tag";
    case ERR_NO_CC: return "capability container missing";
    case ERR_NDEF: return "malformed NDEF data";
    case ERR_REFUSED: return "the tag refused the command or does not support it";
    case ERR_NAK_ARG: return "tag refused the write: invalid argument (bad page address)";
    case ERR_NAK_CRC: return "tag refused the write: parity or CRC error in the command";
    case ERR_NAK_AUTH: return "tag refused the write: authentication counter overflow";
    case ERR_NAK_EEPROM: return "tag refused the write: EEPROM write error";
    case ERR_NAK_OTHER: return "tag refused the write: unknown NAK code";
    default: return "unknown error";
  }
}

// ---- raw access ---------------------------------------------------------
void St25r3916::readRegs(uint8_t reg, uint8_t* out, size_t n) {
  uint8_t op = reg | OP_READ;
  if (!t_.exchange(&op, 1, out, n)) {
    ioError_ = true;
    for (size_t i = 0; i < n; i++) out[i] = 0;
  }
}

uint8_t St25r3916::readReg(uint8_t reg) {
  uint8_t v = 0;
  readRegs(reg, &v, 1);
  return v;
}

void St25r3916::writeReg(uint8_t reg, uint8_t val) {
  uint8_t b[2] = {reg, val};
  if (!t_.write(b, 2)) ioError_ = true;
}

void St25r3916::setBits(uint8_t reg, uint8_t mask) { writeReg(reg, readReg(reg) | mask); }
void St25r3916::clearBits(uint8_t reg, uint8_t mask) { writeReg(reg, readReg(reg) & ~mask); }

uint8_t St25r3916::readSpaceB(uint8_t reg) {
  uint8_t out[2] = {CMD_SPACE_B, (uint8_t)((reg & 0x3F) | OP_READ)};
  uint8_t v = 0;
  if (!t_.exchange(out, 2, &v, 1)) ioError_ = true;
  return v;
}

void St25r3916::writeSpaceB(uint8_t reg, uint8_t val) {
  uint8_t b[3] = {CMD_SPACE_B, (uint8_t)(reg & 0x3F), val};
  if (!t_.write(b, 3)) ioError_ = true;
}

void St25r3916::command(uint8_t cmd, const uint8_t* payload, size_t len, uint32_t delayMs) {
  uint8_t b[8];
  if (len > sizeof(b) - 1) {
    ioError_ = true;
    return;
  }
  b[0] = cmd;
  for (size_t i = 0; i < len; i++) b[1 + i] = payload[i];
  if (!t_.write(b, 1 + len)) ioError_ = true;
  if (delayMs) t_.sleepMs(delayMs);
}

void St25r3916::loadFifo(const uint8_t* data, size_t len) {
  uint8_t b[65];
  if (len > sizeof(b) - 1) {
    ioError_ = true;
    return;
  }
  b[0] = OP_FIFO_LOAD;
  for (size_t i = 0; i < len; i++) b[1 + i] = data[i];
  if (!t_.write(b, 1 + len)) ioError_ = true;
}

size_t St25r3916::fifoCount() {
  uint8_t s[2];
  readRegs(REG_FIFO_STATUS1, s, 2);
  fifoStatus2_ = s[1];
  return s[0] | ((size_t)(s[1] & 0xC0) << 2);
}

uint32_t St25r3916::readIrqs() {
  uint8_t r[4];
  readRegs(REG_IRQ_MAIN, r, 4);
  return r[0] | ((uint32_t)r[1] << 8) | ((uint32_t)r[2] << 16) | ((uint32_t)r[3] << 24);
}

void St25r3916::writeMask(uint32_t value) {
  for (int i = 0; i < 4; i++) writeReg(REG_IRQ_MASK + i, (value >> (24 - 8 * i)) & 0xFF);
}

bool St25r3916::takeIoError() {
  bool e = ioError_;
  ioError_ = false;
  return e;
}

// ---- bring-up -----------------------------------------------------------
int St25r3916::identify(uint8_t* icType, uint8_t* revision) {
  uint8_t raw = readReg(REG_IC_IDENTITY);
  if (takeIoError()) return ERR_I2C;
  if (raw == 0xFF || raw == 0x00) return ERR_NO_CHIP;
  uint8_t type = (raw >> 3) & 0x1F, rev = raw & 0x07;
  if (type != 0x05 || rev == 0) return ERR_NO_CHIP;
  if (icType) *icType = type;
  if (revision) *revision = rev;
  return OK;
}

int St25r3916::init() {
  int rc = identify(nullptr, nullptr);
  if (rc != OK) return rc;
  command(CMD_SET_DEFAULT, nullptr, 0, 10);
  const uint8_t test[2] = {0x04, 0x10};
  command(CMD_TEST_ACCESS, test, 2);
  // Init sequence follows the M5Unit-NFC-Go driver and DS12484.
  writeReg(0x00, 0x10);
  writeReg(0x01, 0x84);
  writeReg(0x28, 0xD0);
  writeReg(0x00, (readReg(0x00) & ~0x07) | 0x07);
  writeSpaceB(0x2A, 0x80);
  setBits(0x01, 0x20);
  writeSpaceB(0x2A, 0x00);
  writeReg(0x2A, 0x13);
  writeReg(0x2B, 0x02);
  writeReg(0x08, (readReg(0x08) & 0x0F) | 0x50);
  writeReg(0x29, 0x5F);
  writeSpaceB(0x05, 0x40);
  writeReg(0x26, 0x82);
  writeReg(0x27, 0x82);
  setBits(0x02, 0x03);
  command(CMD_CLEAR_FIFO);
  writeMask(0xFFFF00FF);
  readIrqs();
  rc = enableOscillator();
  if (rc != OK) return rc;
  writeMask(0x00000000);
  command(CMD_ADJUST_REGULATORS, nullptr, 0, 5);
  rc = configureNfca();
  if (rc != OK) return rc;
  return takeIoError() ? ERR_I2C : OK;
}

int St25r3916::enableOscillator() {
  if (readReg(REG_OP_CTRL) & OP_CTRL_EN) return OK;
  readIrqs();
  setBits(REG_OP_CTRL, OP_CTRL_EN);
  uint32_t deadline = t_.nowMs() + 25;
  bool seen = false;
  while ((int32_t)(t_.nowMs() - deadline) < 0) {
    if (readReg(REG_IRQ_MAIN) & IRQ_OSC) {
      seen = true;
      break;
    }
    t_.sleepMs(1);
  }
  if (!seen) return takeIoError() ? ERR_I2C : ERR_OSC;
  if (!(readReg(REG_AUX_DISPLAY) & AUX_DISPLAY_OSC_OK)) return ERR_OSC;
  return OK;
}

int St25r3916::configureNfca() {
  command(CMD_STOP);
  clearBits(REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN);
  writeReg(REG_MODE, 0x09);
  writeReg(REG_BIT_RATE, 0x00);
  writeReg(REG_ISO14443A, 0x00);
  clearBits(REG_AUX, 0x04);
  static const uint8_t spaceB[][2] = {{0x30, 0x40}, {0x31, 0x03}, {0x32, 0x40},
                                      {0x33, 0x03}, {0x0C, 0x47}, {0x0D, 0x00}};
  for (const auto& rv : spaceB) writeSpaceB(rv[0], rv[1]);
  static const uint8_t spaceA[][2] = {{0x0B, 0x08}, {0x0C, 0x2D}, {0x0D, 0xD8}, {0x0E, 0x22}};
  for (const auto& rv : spaceA) writeReg(rv[0], rv[1]);
  writeMask(0);
  command(CMD_RESET_RX_GAIN);
  rxCrc_ = -1;
  nrt_ = -1;
  fieldOn();
  return takeIoError() ? ERR_I2C : OK;
}

void St25r3916::fieldOn() {
  command(CMD_INITIAL_FIELD_ON, nullptr, 0, 5);
  setBits(REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN);
}

void St25r3916::fieldOff() {
  command(CMD_STOP, nullptr, 0, 5);
  clearBits(REG_OP_CTRL, OP_CTRL_TX_EN | OP_CTRL_RX_EN);
}

void St25r3916::fieldReset(uint32_t offMs) {
  fieldOff();
  t_.sleepMs(offMs);
  fieldOn();
  t_.sleepMs(10);
}

// ---- transceive ---------------------------------------------------------
void St25r3916::setRxCrc(bool check) {
  if (rxCrc_ == (check ? 1 : 0)) return;
  if (check)
    clearBits(REG_AUX, AUX_NO_CRC_RX);
  else
    setBits(REG_AUX, AUX_NO_CRC_RX);
  rxCrc_ = check ? 1 : 0;
}

void St25r3916::setNrt(uint32_t ms) {
  float us = ms * 1000.0f;
  uint32_t steps;
  bool coarse;
  if (us / NRT_FINE_US <= 0xFFFF) {
    long s = lroundf(us / NRT_FINE_US);
    steps = s < 1 ? 1 : (uint32_t)s;
    coarse = false;
  } else {
    long s = lroundf(us / NRT_STEP_US);
    steps = s > 0xFFFF ? 0xFFFF : (uint32_t)s;
    coarse = true;
  }
  int32_t key = (int32_t)((steps << 1) | (coarse ? 1 : 0));
  if (nrt_ == key) return;
  uint8_t timer = readReg(REG_TIMER_EMV);
  writeReg(REG_TIMER_EMV, coarse ? (timer | 0x01) : (timer & ~0x01));
  writeReg(REG_NRT1, steps >> 8);
  writeReg(REG_NRT2, steps & 0xFF);
  nrt_ = key;
}

void St25r3916::prepare(bool rxCrc, uint32_t timeoutMs) {
  command(CMD_CLEAR_FIFO);
  readIrqs();  // clear stale flags
  setRxCrc(rxCrc);
  setNrt(timeoutMs);
}

int St25r3916::collect(uint32_t timeoutMs, uint32_t ignoreFlags, uint8_t* rx, size_t cap,
                       size_t* rxLen, bool shortRx) {
  uint32_t deadline = t_.nowMs() + timeoutMs + 50;
  uint32_t irq = 0;
  for (;;) {
    irq |= readIrqs();
    if (takeIoError()) return ERR_I2C;
    if (irq & IRQ_RXE) break;
    if ((irq & IRQ_NRE) && !(irq & IRQ_RXS)) return ERR_TIMEOUT;
    if ((int32_t)(t_.nowMs() - deadline) > 0) return ERR_TIMEOUT;  // host-side timeout
  }
  irq |= readIrqs();  // late error flags
  if (irq & IRQ_COL) return ERR_COLLISION;
  if (irq & IRQ_ERRORS & ~ignoreFlags) return ERR_FRAME;
  size_t n = fifoCount();
  if (shortRx && (n != 1 || ((fifoStatus2_ >> 1) & 0x07) == 0)) return ERR_FRAME;  // not a 4-bit frame
  if (n > cap) return ERR_PARAM;
  size_t got = 0;
  while (got < n) {
    size_t chunk = n - got > 64 ? 64 : n - got;
    uint8_t op = OP_FIFO_READ;
    if (!t_.exchange(&op, 1, rx + got, chunk)) return ERR_I2C;
    got += chunk;
  }
  *rxLen = n;
  return takeIoError() ? ERR_I2C : OK;
}

int St25r3916::sendShort(uint8_t cmd, uint8_t* rx, size_t cap, size_t* rxLen, uint32_t timeoutMs) {
  prepare(false, timeoutMs);
  command(cmd);
  return collect(timeoutMs, 0, rx, cap, rxLen);
}

int St25r3916::transceive(const uint8_t* tx, size_t txLen, uint8_t* rx, size_t cap, size_t* rxLen,
                          bool txCrc, bool rxCrc, uint32_t timeoutMs, bool shortRx) {
  if (txLen == 0 || txLen > 64) return ERR_PARAM;
  prepare(rxCrc, timeoutMs);
  uint32_t bits = (uint32_t)txLen << 3;
  writeReg(REG_NUM_TX1, bits >> 8);
  writeReg(REG_NUM_TX2, bits & 0xFF);
  loadFifo(tx, txLen);
  command(txCrc ? CMD_TX_WITH_CRC : CMD_TX_WITHOUT_CRC);
  // short_rx: 4-bit ACK frames trip the CRC/parity flags; ignore them.
  int rc = collect(timeoutMs, shortRx ? (IRQ_CRC | IRQ_PAR) : 0, rx, cap, rxLen, shortRx);
  if (rc != OK) return rc;
  if (rxCrc && *rxLen >= 3) {
    // Real silicon leaves the (already checked) CRC_A in the FIFO.
    uint16_t crc = crcA(rx, *rxLen - 2);
    if (rx[*rxLen - 2] == (crc & 0xFF) && rx[*rxLen - 1] == (crc >> 8)) *rxLen -= 2;
  }
  return OK;
}

}  // namespace nfc
