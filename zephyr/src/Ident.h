// NTAG identification helpers, no hardware dependency (host-tested).
// Commands: GET_VERSION 0x60, READ_SIG 0x3C 0x00, READ_CNT 0x39 0x02 (NTAG21x datasheet 10.1, 10.8, 10.6).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace nfc {

enum class Ident : uint8_t { Version = 0x60, Signature = 0x3C, Counter = 0x39 };

// Family name from a GET_VERSION reply (datasheet Table 28).
inline const char* ntagTypeName(const uint8_t* v, size_t len) {
  if (len < 8) return "unknown";
  if (v[1] != 0x04) return "unknown";
  if (v[2] != 0x04) return "NXP, not an NTAG";
  switch (v[6]) {
    case 0x0F: return "NTAG213 (NXP)";
    case 0x11: return "NTAG215 (NXP)";
    case 0x13: return "NTAG216 (NXP)";
    default: return "NTAG, unknown size (NXP)";
  }
}

// READ_CNT returns the 24-bit counter, low byte first.
inline bool counterValue(const uint8_t* b, size_t len, uint32_t* out) {
  if (len != 3) return false;
  *out = (uint32_t)b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16;
  return true;
}

// Meaning of the 4-bit NAK a tag sends after a refused WRITE (datasheet Table 23).
inline const char* nakText(uint8_t code) {
  switch (code) {
    case 0x0: return "tag refused the write: invalid argument (bad page address)";
    case 0x1: return "tag refused the write: parity or CRC error in the command";
    case 0x4: return "tag refused the write: authentication counter overflow";
    case 0x5: return "tag refused the write: EEPROM write error";
    default: return "tag refused the write: unknown NAK code";
  }
}

}  // namespace nfc
