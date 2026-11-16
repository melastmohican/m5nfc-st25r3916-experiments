// ISO 14443-4 (ISO-DEP) block layer for card emulation: the PICC side of RATS/ATS, I-blocks,
// R-blocks, S(DESELECT) and chaining of received commands. No hardware, host-tested
// (test/test_isodeptarget.cpp). The application on top sees whole APDUs.
//
// Limits: no CID, no NAD, 106 kbit/s only, replies are never chained (an application must keep its
// response within kMaxResponse bytes).
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "Target.h"

namespace nfc {

// An application that answers whole APDUs. Returns the response length (including SW1 SW2).
class ApduApp {
 public:
  virtual ~ApduApp() {}
  virtual size_t apdu(const uint8_t* cmd, size_t n, uint8_t* resp, size_t cap) = 0;
  virtual void reset() {}  // a new ISO-DEP session starts (RATS)
};

class IsoDepTarget : public Target {
 public:
  static constexpr size_t kMaxResponse = 250;   // fits one I-block of a 256-byte frame
  static constexpr size_t kMaxCommand = 1024;   // chained commands are collected up to this size

  explicit IsoDepTarget(ApduApp& app) : app_(app) {}

  TargetReply handle(const uint8_t* frame, size_t n, uint8_t* out, size_t cap) override;
  void reset() override;

  bool activated() const { return activated_; }
  // The ATS sent in answer to RATS (TL T0 TA1 TB1 TC1): FSC 256, 106 kbit/s only, FWT about 77 ms.
  static const uint8_t* ats(size_t* len);

 private:
  ApduApp& app_;
  bool activated_ = false;
  uint8_t cmd_[kMaxCommand];
  size_t cmdLen_ = 0;
  uint8_t last_[kMaxResponse + 3];  // last I-block sent, for retransmission after an R(NAK)
  size_t lastLen_ = 0;
  uint8_t lastBn_ = 0;
};

}  // namespace nfc
