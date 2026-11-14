// ST25R3916 NFC front-end driver (M5Stack NFC unit, I2C 0x50).
// The chip logic only talks to a Transport, so it does not depend on Arduino's Wire.
//
// I2C frame formats:
//   register read   write [reg|0x40] then read N bytes (auto-increment)
//   register write  write [reg, val]
//   space-B read    write [0xFB, (reg&0x3F)|0x40] then read 1
//   space-B write   write [0xFB, reg&0x3F, val]
//   direct command  write [cmd]
//   FIFO load       write [0x80, data...]
//   FIFO read       write [0x9F] then read N bytes
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace nfc {

enum Status : int {
  OK = 0,
  ERR_TIMEOUT = -1,       // no response from the card
  ERR_COLLISION = -2,
  ERR_FRAME = -3,         // CRC / parity / framing error on receive
  ERR_I2C = -4,
  ERR_BAD_RESPONSE = -5,
  ERR_NO_CHIP = -6,
  ERR_OSC = -7,
  ERR_PARAM = -8,
  ERR_NO_ACK = -9,
  ERR_PROTECTED = -10,    // page is read-only, out of range, or lock/config/CC without force
  ERR_VERIFY = -11,       // read-back after a write differs
  ERR_TOO_LARGE = -12,    // NDEF message does not fit the tag
  ERR_NO_CC = -13,        // capability container missing
  ERR_NDEF = -14,         // malformed NDEF data
};

const char* statusName(int status);

class Transport {
 public:
  virtual ~Transport() {}
  virtual bool write(const uint8_t* data, size_t len) = 0;
  // write `out`, repeated start, read `inLen` bytes into `in`
  virtual bool exchange(const uint8_t* out, size_t outLen, uint8_t* in, size_t inLen) = 0;
  virtual void sleepMs(uint32_t ms) = 0;
  virtual uint32_t nowMs() = 0;
};

// Direct commands used by other layers
constexpr uint8_t CMD_TX_REQA = 0xC6;
constexpr uint8_t CMD_TX_WUPA = 0xC7;

class St25r3916 {
 public:
  explicit St25r3916(Transport& t) : t_(t) {}

  // ---- bring-up
  int identify(uint8_t* icType, uint8_t* revision);
  int init();  // reset, configure, NFC-A reader mode, field on
  void fieldOn();
  void fieldOff();
  void fieldReset(uint32_t offMs = 20);  // power-cycle the RF field so cards restart

  // ---- cards
  // REQA/WUPA: returns the 2-byte ATQA in rx.
  int sendShort(uint8_t cmd, uint8_t* rx, size_t cap, size_t* rxLen, uint32_t timeoutMs = 5);
  // Send whole bytes, return the card's response.
  // txCrc: chip appends CRC_A. rxCrc: chip checks CRC_A and we strip it.
  // `rx` must have 2 spare bytes when rxCrc is set: the chip leaves CRC_A in the FIFO and it is
  // only stripped after the whole frame has been read. Returns ERR_PARAM if the frame is larger.
  // shortRx: a 4-bit frame (the WRITE ACK) trips the CRC/parity flags; ignore them.
  int transceive(const uint8_t* tx, size_t txLen, uint8_t* rx, size_t cap, size_t* rxLen,
                 bool txCrc = true, bool rxCrc = true, uint32_t timeoutMs = 10,
                 bool shortRx = false);

  // ---- raw access (also useful for debugging)
  void readRegs(uint8_t reg, uint8_t* out, size_t n);
  uint8_t readReg(uint8_t reg);
  void writeReg(uint8_t reg, uint8_t val);
  void setBits(uint8_t reg, uint8_t mask);
  void clearBits(uint8_t reg, uint8_t mask);
  uint8_t readSpaceB(uint8_t reg);
  void writeSpaceB(uint8_t reg, uint8_t val);
  void command(uint8_t cmd, const uint8_t* payload = nullptr, size_t len = 0, uint32_t delayMs = 0);
  // true if any I2C transfer failed since the last call (cleared by the call)
  bool takeIoError();

 private:
  void loadFifo(const uint8_t* data, size_t len);
  size_t fifoCount();
  uint32_t readIrqs();
  void writeMask(uint32_t value);
  int enableOscillator();
  int configureNfca();
  void setRxCrc(bool check);
  void setNrt(uint32_t ms);
  void prepare(bool rxCrc, uint32_t timeoutMs);
  int collect(uint32_t timeoutMs, uint32_t ignoreFlags, uint8_t* rx, size_t cap, size_t* rxLen);

  Transport& t_;
  int8_t rxCrc_ = -1;   // -1 unknown, 0 off, 1 on
  int32_t nrt_ = -1;    // cached no-response timer setting
  bool ioError_ = false;
};

}  // namespace nfc
