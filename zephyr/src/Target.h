// What a card-emulation target answers to one command frame (see Listener.h). No hardware.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace nfc {

struct TargetReply {
  size_t len = 0;       // reply bytes (the chip adds the CRC); 0 with ack4 == false: no reply
  bool ack4 = false;    // send the 4-bit frame `nibble` instead (Type 2: ACK = 0xA, NAK = 0x0)
  uint8_t nibble = 0;
  bool halt = false;    // go to the halt state after sending the reply (HLTA, DESELECT)
};

class Target {
 public:
  virtual ~Target() {}
  // One frame from the reader, CRC removed. `out` has room for `cap` bytes (at least 260).
  virtual TargetReply handle(const uint8_t* frame, size_t n, uint8_t* out, size_t cap) = 0;
  // A new session starts (the reader selected us again): forget any protocol state.
  virtual void reset() {}
};

}  // namespace nfc
