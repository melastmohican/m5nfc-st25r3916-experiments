#include <stdio.h>
#include <string.h>

#include "../src/Type2Tag.h"

static int fails = 0;
#define CHECK(c)                                          \
  do {                                                    \
    if (!(c)) {                                           \
      printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
      fails++;                                            \
    }                                                     \
  } while (0)

int main() {
  const uint8_t uid[7] = {0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
  nfc::Type2Tag tag(uid);
  uint8_t out[64];

  // READ page 0: UID with BCC bytes
  auto r = tag.handle((const uint8_t[]){0x30, 0x00}, 2, out, sizeof(out));
  CHECK(r.len == 16 && !r.ack4);
  CHECK(out[0] == 0x04 && out[3] == (0x88 ^ 0x04 ^ 0x11 ^ 0x22));
  CHECK(out[4] == 0x33 && out[8] == (0x33 ^ 0x44 ^ 0x55 ^ 0x66));
  CHECK(out[12] == 0xE1 && out[13] == 0x10 && out[14] == 0x3E);

  // a blank NTAG215: AUTH0 = FF (no password protection), PWD = FF FF FF FF
  r = tag.handle((const uint8_t[]){0x30, 0x83}, 2, out, sizeof(out));
  CHECK(r.len == 16 && out[3] == 0xFF && out[8] == 0xFF && out[11] == 0xFF);

  // READ wraps past the last page
  r = tag.handle((const uint8_t[]){0x30, 0x86}, 2, out, sizeof(out));  // page 134
  CHECK(r.len == 16 && memcmp(out + 4, tag.memory(), 12) == 0);
  // READ out of range
  r = tag.handle((const uint8_t[]){0x30, 0x87}, 2, out, sizeof(out));
  CHECK(r.len == 0 && r.ack4 && r.nibble == 0x0);

  // FAST_READ pages 3..5
  r = tag.handle((const uint8_t[]){0x3A, 0x03, 0x05}, 3, out, sizeof(out));
  CHECK(r.len == 12 && out[0] == 0xE1);
  r = tag.handle((const uint8_t[]){0x3A, 0x05, 0x03}, 3, out, sizeof(out));
  CHECK(r.ack4 && r.nibble == 0x0);

  // WRITE: ACK, stored, marked changed
  CHECK(!tag.changed(4));
  r = tag.handle((const uint8_t[]){0xA2, 0x04, 0x03, 0x0A, 0xD1, 0x01}, 6, out, sizeof(out));
  CHECK(r.ack4 && r.nibble == 0xA && tag.writeCount() == 1);
  CHECK(tag.memory()[16] == 0x03 && tag.memory()[19] == 0x01 && tag.changed(4) && !tag.changed(5));
  // writing the same data again is still ACKed but does not mark the page again
  tag.clearChanged();
  r = tag.handle((const uint8_t[]){0xA2, 0x04, 0x03, 0x0A, 0xD1, 0x01}, 6, out, sizeof(out));
  CHECK(r.ack4 && r.nibble == 0xA && !tag.changed(4));
  // UID and lock pages are refused
  r = tag.handle((const uint8_t[]){0xA2, 0x00, 1, 2, 3, 4}, 6, out, sizeof(out));
  CHECK(r.ack4 && r.nibble == 0x0 && tag.memory()[0] == 0x04);
  r = tag.handle((const uint8_t[]){0xA2, 0x02, 1, 2, 3, 4}, 6, out, sizeof(out));
  CHECK(r.ack4 && r.nibble == 0x0);
  // wrong length
  r = tag.handle((const uint8_t[]){0xA2, 0x04, 1, 2, 3}, 5, out, sizeof(out));
  CHECK(r.ack4 && r.nibble == 0x1);

  // GET_VERSION, READ_SIG
  r = tag.handle((const uint8_t[]){0x60}, 1, out, sizeof(out));
  CHECK(r.len == 8 && out[2] == 0x04 && out[6] == 0x11);
  r = tag.handle((const uint8_t[]){0x3C, 0x00}, 2, out, sizeof(out));
  CHECK(r.len == 32);

  // HLTA ends the session without a reply
  r = tag.handle((const uint8_t[]){0x50, 0x00}, 2, out, sizeof(out));
  CHECK(r.halt && r.len == 0 && !r.ack4);

  // unknown command: NAK
  r = tag.handle((const uint8_t[]){0x99}, 1, out, sizeof(out));
  CHECK(r.ack4 && r.nibble == 0x0);

  if (fails == 0) printf("all Type 2 tag checks passed\n");
  return fails ? 1 : 0;
}
