// CCID message handling, independent of USB and of the NFC chip.
// C++ port of micropython/ccid_proto.py. Plain C++: builds on the Mac for host tests.
//
// Slot::handle() turns one PC_to_RDR bulk message into the RDR_to_PC reply, talking to a Backend.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace nfc {
namespace ccid {

// PC_to_RDR
constexpr uint8_t ICC_POWER_ON = 0x62;
constexpr uint8_t ICC_POWER_OFF = 0x63;
constexpr uint8_t GET_SLOT_STATUS = 0x65;
constexpr uint8_t XFR_BLOCK = 0x6F;
constexpr uint8_t GET_PARAMETERS = 0x6C;
constexpr uint8_t SET_PARAMETERS = 0x61;
constexpr uint8_t RESET_PARAMETERS = 0x6D;
constexpr uint8_t ESCAPE = 0x6B;
// RDR_to_PC
constexpr uint8_t DATA_BLOCK = 0x80;
constexpr uint8_t SLOT_STATUS = 0x81;
constexpr uint8_t PARAMETERS = 0x82;
constexpr uint8_t ESCAPE_RESP = 0x83;
constexpr uint8_t NOTIFY_SLOT_CHANGE = 0x50;

// bStatus: bits 0-1 = ICC status, bits 6-7 = command status (1 = failed)
constexpr uint8_t ICC_ACTIVE = 0;
constexpr uint8_t ICC_INACTIVE = 1;
constexpr uint8_t ICC_ABSENT = 2;
constexpr uint8_t CMD_OK = 0x00;
constexpr uint8_t CMD_FAILED = 0x40;

// bError
constexpr uint8_t ERR_CMD_NOT_SUPPORTED = 0x00;
constexpr uint8_t ERR_SLOT = 0x05;
constexpr uint8_t ERR_ICC_MUTE = 0xFE;
constexpr uint8_t ERR_HW = 0xFB;

constexpr size_t HEADER_LEN = 10;
constexpr size_t MAX_MESSAGE = 271;  // header + 261, as announced in the class descriptor

// Results of Backend calls
enum BackendResult : int { BE_OK = 0, BE_ABSENT = -1, BE_ERROR = -2 };

class Backend {
 public:
  virtual ~Backend() {}
  // ICC_ACTIVE / ICC_INACTIVE / ICC_ABSENT, or a negative BackendResult on a chip error
  virtual int status() = 0;
  virtual int powerOn(uint8_t* atr, size_t cap, size_t* len) = 0;
  virtual void powerOff() = 0;
  // apdu in, response (including SW1 SW2) out
  virtual int transmit(const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap, size_t* respLen) = 0;
};

// PC/SC part 3 ATR for an ISO 14443-A part 3 card (MIFARE Ultralight family): 20 bytes
size_t contactlessAtr(uint8_t* out, size_t cap);

// RDR_to_PC_NotifySlotChange: 2 bytes
void slotChangeMessage(bool present, bool changed, uint8_t out[2]);

class Slot {
 public:
  explicit Slot(Backend& backend) : be_(backend) {}
  // Returns the length of the reply written to `out` (0 = no reply).
  size_t handle(const uint8_t* msg, size_t n, uint8_t* out, size_t cap);

 private:
  size_t reply(uint8_t type, uint8_t slot, uint8_t seq, uint8_t status, uint8_t error,
               uint8_t specific, const uint8_t* data, size_t dlen, uint8_t* out, size_t cap);
  size_t failed(uint8_t type, uint8_t slot, uint8_t seq, uint8_t icc, uint8_t error, uint8_t* out,
                size_t cap);
  uint8_t iccStatus();

  Backend& be_;
};

}  // namespace ccid
}  // namespace nfc
