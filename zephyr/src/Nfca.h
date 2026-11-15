// ISO14443-A activation (WUPA, anticollision, select) and Type 2 tag READ/WRITE.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "St25r3916.h"

namespace nfc {

struct Card {
  uint8_t uid[10];
  uint8_t uidLen;
  uint8_t atqa[2];
  uint8_t sak;
};

// "04:64:31:66:D3:2A:81" into out (needs 3 * uidLen bytes)
void uidToHex(const Card& card, char* out, size_t cap);

// ISO14443-A CRC, low byte first
void crcA(const uint8_t* data, size_t len, uint8_t out[2]);

// WUPA + cascade-level anticollision/select. Supports 4- and 7-byte UIDs.
// A tag left in ACTIVE state by an earlier select ignores WUPA until its field is
// power-cycled, so on a timeout the field is reset once and WUPA is retried.
int selectCard(St25r3916& rf, Card& card);

// READ returns 4 pages (16 bytes) starting at `page`.
int t2Read(St25r3916& rf, uint8_t page, uint8_t out[16]);
// WRITE one 4-byte page.
int t2Write(St25r3916& rf, uint8_t page, const uint8_t data[4]);

}  // namespace nfc
