// Applications for the ISO-DEP target (IsoDepTarget.h). No hardware, host-tested.
//
//   Type4NdefApp  an NFC Forum Type 4 tag: SELECT the NDEF application (D2760000850101), the
//                 capability container E103 and the NDEF file E104, READ BINARY and UPDATE BINARY.
//   SniffApp      records every APDU in full and answers 90 00 to everything, so an app that
//                 sends a vendor protocol (for example F0 D2 image packets) keeps talking.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "IsoDepTarget.h"

namespace nfc {

class Type4NdefApp : public ApduApp {
 public:
  static constexpr size_t NDEF_FILE_SIZE = 1024;  // NLEN (2 bytes) + message
  static constexpr size_t MAX_READ = 0xF0;        // MLe / MLc announced in the capability container

  Type4NdefApp();
  size_t apdu(const uint8_t* cmd, size_t n, uint8_t* resp, size_t cap) override;
  void reset() override { selected_ = NONE; }

  const uint8_t* ndefFile() const { return ndef_; }  // NLEN + message
  size_t updateCount() const { return updates_; }

 private:
  enum Selected : uint8_t { NONE, APP, CC, NDEF_FILE };
  uint8_t cc_[15];
  uint8_t ndef_[NDEF_FILE_SIZE];
  Selected selected_ = NONE;
  bool appSelected_ = false;
  size_t updates_ = 0;
};

class SniffApp : public ApduApp {
 public:
  static constexpr size_t CAPTURE_SIZE = 16384;

  size_t apdu(const uint8_t* cmd, size_t n, uint8_t* resp, size_t cap) override;
  void reset() override { sessions_++; }

  // The capture is a list of records: [1 = command, 2 = response][length hi][length lo][bytes].
  const uint8_t* capture() const { return buf_; }
  size_t captureSize() const { return used_; }
  size_t apduCount() const { return apdus_; }
  size_t sessionCount() const { return sessions_; }
  bool full() const { return full_; }
  void clear() { used_ = apdus_ = sessions_ = 0; full_ = false; }

 private:
  void record(uint8_t kind, const uint8_t* data, size_t n);
  uint8_t buf_[CAPTURE_SIZE];
  size_t used_ = 0;
  size_t apdus_ = 0;
  size_t sessions_ = 0;
  bool full_ = false;
};

}  // namespace nfc
