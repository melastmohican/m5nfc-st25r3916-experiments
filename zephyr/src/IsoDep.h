// ISO-DEP (ISO/IEC 14443-4) block protocol for the reader side: RATS/ATS, I-block framing with the
// block-number toggle, chaining in both directions, waiting-time extension, retries and deselect.
//
// Port of the ISO-DEP layer in embassy-rs/rnfc (MIT or Apache-2.0), checked with the same
// ISO 14443-4 Annex B scenarios (test/test_isodep.cpp). No hardware dependency: the
// frames go through an IsoDepLink.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "St25r3916.h"  // status codes only

namespace nfc {

// One ISO 14443-A frame exchange. The link adds CRC_A when sending and checks and strips it on
// receive. Timeouts are in carrier clocks (1/13.56 MHz). Return ERR_TIMEOUT, ERR_FRAME or
// ERR_COLLISION for a lost or damaged frame (the protocol retries those), another status for a
// hard failure.
class IsoDepLink {
 public:
  virtual ~IsoDepLink() {}
  virtual int transceive(const uint8_t* tx, size_t txLen, uint8_t* rx, size_t cap, size_t* rxLen,
                         uint32_t timeout1fc) = 0;
  virtual void sleepMs(uint32_t ms) = 0;
};

class IsoDep {
 public:
  static constexpr size_t ATS_MAX = 32;

  explicit IsoDep(IsoDepLink& link) : link_(link) {}

  // Sends RATS (E0 80) and reads the ATS. The card must already be selected.
  int activate();
  // One command/response exchange of any length; handles chaining and waiting-time extension.
  int transceive(const uint8_t* tx, size_t txLen, uint8_t* rx, size_t cap, size_t* rxLen);
  // S(DESELECT): ends the session, the card goes back to idle.
  int deselect();

  const uint8_t* ats() const { return ats_; }
  size_t atsLen() const { return atsLen_; }
  size_t fsc() const { return fsc_; }
  uint32_t sfgt1fc() const { return sfgt1fc_; }
  uint32_t fwt1fc() const { return fwt1fc_; }
  void setFscForTest(size_t fsc) { fsc_ = fsc; }

 private:
  IsoDepLink& link_;
  uint8_t ats_[ATS_MAX] = {};
  size_t atsLen_ = 0;
  size_t fsc_ = 32;          // largest frame the card accepts, including header and CRC
  uint32_t sfgt1fc_ = 4096;  // start-up frame guard time
  uint32_t fwt1fc_ = 65536;  // frame waiting time
  uint8_t blockNum_ = 0;
};

}  // namespace nfc
