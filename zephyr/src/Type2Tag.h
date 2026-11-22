// An NTAG215-like NFC Forum Type 2 tag in RAM, for card emulation (see Listener.h).
// Handles one command frame (CRC already removed) and says what to send back. No hardware and no
// Arduino dependency, so it is host-tested (test/test_type2tag.cpp).
//
//   READ 30 pp        16 bytes (wraps past the last page, as a real tag does)
//   FAST_READ 3A s e  pages s..e
//   WRITE A2 pp d0..3 ACK; pages 0-2 (UID, lock) are refused with a NAK
//   GET_VERSION 60    NTAG215: 00 04 04 02 01 00 11 03
//   READ_SIG 3C 00    32 zero bytes (an emulated tag has no signature)
//   HLTA 50 00        no reply, the tag goes to the halt state
//   anything else     NAK
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "Target.h"

namespace nfc {

class Type2Tag : public Target {
 public:
  static constexpr size_t PAGES = 135;  // NTAG215
  static constexpr size_t BYTES = PAGES * 4;

  using Reply = TargetReply;

  // 7-byte UID (BCC bytes are computed), capability container E1 10 3E 00, empty NDEF message.
  explicit Type2Tag(const uint8_t uid[7]);

  // Fills `out` and returns it. `cap` must be at least 64.
  Reply handle(const uint8_t* frame, size_t n, uint8_t* out, size_t cap) override;

  const uint8_t* memory() const { return mem_; }
  size_t writeCount() const { return writes_; }
  // One bit per page, set when a WRITE changed it since construction or clearChanged().
  bool changed(size_t page) const { return page < PAGES && (changed_[page >> 3] >> (page & 7)) & 1; }
  void clearChanged();

 private:
  uint8_t mem_[BYTES];
  uint8_t changed_[(PAGES + 7) / 8];
  size_t writes_ = 0;
};

}  // namespace nfc
