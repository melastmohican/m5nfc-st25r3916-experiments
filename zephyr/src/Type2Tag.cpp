#include "Type2Tag.h"

#include <string.h>

namespace nfc {

Type2Tag::Type2Tag(const uint8_t uid[7]) {
  memset(mem_, 0, sizeof(mem_));
  memset(changed_, 0, sizeof(changed_));
  memcpy(mem_, uid, 3);
  mem_[3] = 0x88 ^ uid[0] ^ uid[1] ^ uid[2];  // BCC0 (cascade tag 0x88)
  memcpy(mem_ + 4, uid + 3, 4);
  mem_[8] = uid[3] ^ uid[4] ^ uid[5] ^ uid[6];  // BCC1
  mem_[10] = 0x00;                              // lock bytes
  mem_[11] = 0x00;
  mem_[12] = 0xE1;  // capability container: NDEF, version 1.0, 496 bytes
  mem_[13] = 0x10;
  mem_[14] = 0x3E;
  mem_[15] = 0x00;
  // Configuration pages of a blank NTAG215: no protection (AUTH0 = FF), default password and PACK.
  mem_[130 * 4 + 3] = 0xBD;  // dynamic lock bytes
  mem_[131 * 4 + 3] = 0xFF;  // AUTH0: no page needs the password
  mem_[132 * 4 + 1] = 0x05;  // ACCESS / VCTID
  memset(mem_ + 133 * 4, 0xFF, 4);  // PWD
  mem_[16] = 0x03;  // empty NDEF TLV
  mem_[17] = 0x00;
  mem_[18] = 0xFE;
}

void Type2Tag::clearChanged() { memset(changed_, 0, sizeof(changed_)); }

Type2Tag::Reply Type2Tag::handle(const uint8_t* f, size_t n, uint8_t* out, size_t cap) {
  Reply r;
  auto nak = [&r](uint8_t code) {
    r.ack4 = true;
    r.nibble = code;
    return r;
  };
  if (n == 0 || cap < 64) return r;
  switch (f[0]) {
    case 0x30:  // READ
      if (n != 2 || f[1] >= PAGES) return nak(0x0);
      for (size_t i = 0; i < 16; i++) out[i] = mem_[(f[1] * 4 + i) % BYTES];
      r.len = 16;
      return r;
    case 0x3A: {  // FAST_READ
      if (n != 3 || f[1] > f[2] || f[2] >= PAGES) return nak(0x0);
      size_t bytes = (size_t)(f[2] - f[1] + 1) * 4;
      if (bytes > cap) return nak(0x0);
      memcpy(out, mem_ + f[1] * 4, bytes);
      r.len = bytes;
      return r;
    }
    case 0xA2:  // WRITE
      if (n != 6) return nak(0x1);
      if (f[1] < 3 || f[1] >= PAGES) return nak(0x0);
      if (memcmp(mem_ + f[1] * 4, f + 2, 4) != 0) {
        memcpy(mem_ + f[1] * 4, f + 2, 4);
        changed_[f[1] >> 3] |= (uint8_t)(1u << (f[1] & 7));
      }
      writes_++;
      r.ack4 = true;
      r.nibble = 0xA;
      return r;
    case 0x60: {  // GET_VERSION (NTAG215)
      static const uint8_t v[8] = {0x00, 0x04, 0x04, 0x02, 0x01, 0x00, 0x11, 0x03};
      memcpy(out, v, 8);
      r.len = 8;
      return r;
    }
    case 0x3C:  // READ_SIG
      memset(out, 0, 32);
      r.len = 32;
      return r;
    case 0x50:  // HLTA
      if (n == 2 && f[1] == 0x00) {
        r.halt = true;
        return r;
      }
      return nak(0x0);
    default:
      return nak(0x0);
  }
}

}  // namespace nfc
