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

namespace nfc {

class Type2Tag {
 public:
  static constexpr size_t PAGES = 135;  // NTAG215
  static constexpr size_t BYTES = PAGES * 4;

  struct Reply {
    size_t len = 0;       // reply bytes (CRC is added by the chip); 0 with ack4 == false: no reply
    bool ack4 = false;    // send the 4-bit frame `nibble` instead (ACK = 0xA, NAK = 0x0)
    uint8_t nibble = 0;
    bool halt = false;    // the reader sent HLTA
  };

  // 7-byte UID (BCC bytes are computed), capability container E1 10 3E 00, empty NDEF message.
  explicit Type2Tag(const uint8_t uid[7]);

  // Fills `out` and returns it. `cap` must be at least 64.
  Reply handle(const uint8_t* frame, size_t n, uint8_t* out, size_t cap);

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
