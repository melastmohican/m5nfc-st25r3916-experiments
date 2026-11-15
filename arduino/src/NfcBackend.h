// CCID backend on the NFC unit: maps PC/SC pseudo-APDUs onto an NTAG/Ultralight tag.
//
//   FF CA 00 00 Le          -> UID (Le = 0: whole UID)
//   FF B0 00 <page> <Le>    -> read Le bytes starting at <page>  (Le = 0: 16 bytes)
//   FF D6 00 <page> 04 <d>  -> write one 4-byte page
//
// Lock/config pages are refused (Ntag215::writePage guard) with SW 69 82.
#pragma once
#include "CcidProto.h"
#include "Nfca.h"
#include "Ntag215.h"
#include "St25r3916.h"

namespace nfc {

class NfcBackend : public ccid::Backend {
 public:
  explicit NfcBackend(St25r3916& rf) : rf_(rf), tag_(rf) {}

  int status() override;
  int powerOn(uint8_t* atr, size_t cap, size_t* len) override;
  void powerOff() override;
  int transmit(const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap, size_t* respLen) override;

 private:
  int ident(const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap, size_t* n);
  int getData(uint8_t p1, const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap, size_t* n);
  int readBinary(uint8_t page, const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap,
                 size_t* n);
  int updateBinary(uint8_t page, const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap,
                   size_t* n);
  bool probe();

  St25r3916& rf_;
  Ntag215 tag_;
  Card card_;
  bool powered_ = false;  // set while the host holds the slot powered
};

}  // namespace nfc
