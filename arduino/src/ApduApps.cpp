#include "ApduApps.h"

#include <string.h>

namespace nfc {

namespace {
size_t sw(uint8_t* resp, uint8_t sw1, uint8_t sw2) {
  resp[0] = sw1;
  resp[1] = sw2;
  return 2;
}
const uint8_t kNdefAid[7] = {0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01};
}  // namespace

// ---- Type 4 NDEF tag ------------------------------------------------------------------------
Type4NdefApp::Type4NdefApp() {
  static const uint8_t cc[15] = {0x00, 0x0F,                          // CCLEN
                                 0x20,                                // mapping version 2.0
                                 0x00, MAX_READ, 0x00, MAX_READ,      // MLe, MLc
                                 0x04, 0x06, 0xE1, 0x04,              // NDEF file control TLV: file E104
                                 NDEF_FILE_SIZE >> 8, NDEF_FILE_SIZE & 0xFF,  // maximum NDEF file size
                                 0x00, 0x00};                         // read and write access: open
  memcpy(cc_, cc, sizeof(cc));
  memset(ndef_, 0, sizeof(ndef_));  // NLEN = 0: empty message
}

size_t Type4NdefApp::apdu(const uint8_t* c, size_t n, uint8_t* resp, size_t cap) {
  if (n < 4 || cap < MAX_READ + 2) return sw(resp, 0x67, 0x00);
  if (c[0] != 0x00) return sw(resp, 0x6E, 0x00);
  const uint8_t ins = c[1], p1 = c[2], p2 = c[3];

  if (ins == 0xA4) {  // SELECT
    size_t lc = n > 4 ? c[4] : 0;
    if (n >= 5 + lc && p1 == 0x04) {  // by name: the NDEF application
      if (lc == sizeof(kNdefAid) && memcmp(c + 5, kNdefAid, lc) == 0) {
        appSelected_ = true;
        selected_ = APP;
        return sw(resp, 0x90, 0x00);
      }
      return sw(resp, 0x6A, 0x82);
    }
    if (p1 == 0x00 && lc == 2 && n >= 7 && appSelected_) {  // by file id
      uint16_t id = (uint16_t)c[5] << 8 | c[6];
      if (id == 0xE103) {
        selected_ = CC;
        return sw(resp, 0x90, 0x00);
      }
      if (id == 0xE104) {
        selected_ = NDEF_FILE;
        return sw(resp, 0x90, 0x00);
      }
      return sw(resp, 0x6A, 0x82);
    }
    return sw(resp, 0x6A, 0x86);
  }

  if (ins == 0xB0) {  // READ BINARY
    if (selected_ != CC && selected_ != NDEF_FILE) return sw(resp, 0x69, 0x86);
    if (p1 & 0x80) return sw(resp, 0x6B, 0x00);
    size_t off = (size_t)p1 << 8 | p2;
    size_t le = n > 4 ? c[4] : 0;
    if (le == 0 || le > MAX_READ) le = MAX_READ;
    const uint8_t* file = selected_ == CC ? cc_ : ndef_;
    size_t size = selected_ == CC ? sizeof(cc_) : sizeof(ndef_);
    if (off > size) return sw(resp, 0x6B, 0x00);
    if (off + le > size) le = size - off;
    memcpy(resp, file + off, le);
    resp[le] = 0x90;
    resp[le + 1] = 0x00;
    return le + 2;
  }

  if (ins == 0xD6) {  // UPDATE BINARY
    if (selected_ != NDEF_FILE) return sw(resp, selected_ == CC ? 0x69 : 0x69, selected_ == CC ? 0x82 : 0x86);
    if (p1 & 0x80) return sw(resp, 0x6B, 0x00);
    size_t off = (size_t)p1 << 8 | p2;
    size_t lc = n > 4 ? c[4] : 0;
    if (n != 5 + lc || lc == 0 || lc > MAX_READ) return sw(resp, 0x67, 0x00);
    if (off + lc > sizeof(ndef_)) return sw(resp, 0x6B, 0x00);
    memcpy(ndef_ + off, c + 5, lc);
    updates_++;
    return sw(resp, 0x90, 0x00);
  }
  return sw(resp, 0x6D, 0x00);
}

// ---- sniffer --------------------------------------------------------------------------------
void SniffApp::record(uint8_t kind, const uint8_t* data, size_t n) {
  if (used_ + 3 + n > CAPTURE_SIZE) {
    full_ = true;
    return;
  }
  buf_[used_++] = kind;
  buf_[used_++] = n >> 8;
  buf_[used_++] = n & 0xFF;
  memcpy(buf_ + used_, data, n);
  used_ += n;
}

size_t SniffApp::apdu(const uint8_t* c, size_t n, uint8_t* resp, size_t cap) {
  (void)cap;
  apdus_++;
  record(1, c, n);
  size_t rl;
  if (n >= 4 && c[0] == 0xF0 && c[1] == 0xDE) {  // vendor "get busy": idle
    resp[0] = 0x00;
    resp[1] = 0x90;
    resp[2] = 0x00;
    rl = 3;
  } else {
    resp[0] = 0x90;
    resp[1] = 0x00;
    rl = 2;
  }
  record(2, resp, rl);
  return rl;
}

}  // namespace nfc
