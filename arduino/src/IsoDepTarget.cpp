#include "IsoDepTarget.h"

#include <string.h>

namespace nfc {

namespace {
const uint8_t kAts[] = {0x05, 0x78, 0x80, 0x80, 0x00};
}

const uint8_t* IsoDepTarget::ats(size_t* len) {
  *len = sizeof(kAts);
  return kAts;
}

void IsoDepTarget::reset() {
  activated_ = false;
  cmdLen_ = 0;
  lastLen_ = 0;
  lastBn_ = 0;
  app_.reset();
}

TargetReply IsoDepTarget::handle(const uint8_t* f, size_t n, uint8_t* out, size_t cap) {
  TargetReply r;
  if (n == 0 || cap < kMaxResponse + 3) return r;

  if (f[0] == 0xE0) {  // RATS: E0 <FSDI|CID>
    if (n != 2) return r;
    reset();
    activated_ = true;
    memcpy(out, kAts, sizeof(kAts));
    r.len = sizeof(kAts);
    return r;
  }
  if (!activated_) return r;

  const uint8_t pcb = f[0];
  const uint8_t bn = pcb & 0x01;

  if ((pcb & 0xC2) == 0x02) {  // I-block (bit 2 is always 1)
    if (pcb & 0x0C) return r;  // CID or NAD present: not supported, stay silent
    const uint8_t* info = f + 1;
    size_t infoLen = n - 1;
    if (cmdLen_ + infoLen > kMaxCommand) {
      cmdLen_ = 0;
      return r;
    }
    memcpy(cmd_ + cmdLen_, info, infoLen);
    cmdLen_ += infoLen;
    if (pcb & 0x10) {  // chaining: ask for the next block
      out[0] = 0xA2 | bn;
      r.len = 1;
      return r;
    }
    size_t rl = app_.apdu(cmd_, cmdLen_, out + 1, kMaxResponse);
    cmdLen_ = 0;
    if (rl > kMaxResponse) rl = 0;
    out[0] = 0x02 | bn;
    r.len = rl + 1;
    memcpy(last_, out, r.len);
    lastLen_ = r.len;
    lastBn_ = bn;
    return r;
  }

  if ((pcb & 0xC2) == 0x82) {  // R-block: ACK (0xA2/0xA3) or NAK (0xB2/0xB3)
    if (pcb & 0x10) {          // NAK: repeat the last I-block, or acknowledge if we have none
      if (lastLen_ && bn == lastBn_) {
        memcpy(out, last_, lastLen_);
        r.len = lastLen_;
      } else {
        out[0] = 0xA2 | bn;
        r.len = 1;
      }
    } else if (lastLen_ && bn == lastBn_) {  // ACK for a block we already sent: repeat it
      memcpy(out, last_, lastLen_);
      r.len = lastLen_;
    }
    return r;
  }

  if ((pcb & 0xC7) == 0xC2) {  // S-block
    if ((pcb & 0x30) == 0x00) {  // DESELECT: answer and leave the session
      out[0] = 0xC2;
      r.len = 1;
      r.halt = true;
      activated_ = false;
      cmdLen_ = 0;
      return r;
    }
  }
  return r;
}

}  // namespace nfc
