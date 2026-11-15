// DESFire native commands wrapped in ISO 7816 APDUs (90 INS 00 00 00), over an activated IsoDep.
// No hardware or Arduino dependency (host-tested).
#pragma once
#include <string.h>

#include "IsoDep.h"

namespace nfc {

// Runs one command and follows "additional frame" replies (SW 91 AF, sent again as 90 AF 00 00 00).
// Appends the data of all frames to `out` and returns the final SW1 SW2.
inline int desfireCommand(IsoDep& dep, uint8_t ins, uint8_t* out, size_t cap, size_t* outLen,
                          uint8_t* sw1, uint8_t* sw2) {
  *outLen = 0;
  for (int frames = 0; frames < 32; frames++) {
    const uint8_t apdu[5] = {0x90, ins, 0x00, 0x00, 0x00};
    uint8_t rx[260];
    size_t n = 0;
    int rc = dep.transceive(apdu, sizeof(apdu), rx, sizeof(rx), &n);
    if (rc != OK) return rc;
    if (n < 2) return ERR_PROTOCOL;
    if (*outLen + (n - 2) > cap) return ERR_PARAM;
    memcpy(out + *outLen, rx, n - 2);
    *outLen += n - 2;
    *sw1 = rx[n - 2];
    *sw2 = rx[n - 1];
    if (!(*sw1 == 0x91 && *sw2 == 0xAF)) return OK;
    ins = 0xAF;
  }
  return ERR_PROTOCOL;  // a card that never stops asking for more
}

}  // namespace nfc
