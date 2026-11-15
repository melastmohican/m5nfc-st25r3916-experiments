// Host tests for the CCID message layer (no Arduino needed):  ./test/run.sh
#include <initializer_list>
#include <stdio.h>
#include <string.h>

#include "../src/CcidProto.h"

using namespace nfc::ccid;

static int failures = 0;
#define CHECK(cond)                                                      \
  do {                                                                   \
    if (!(cond)) {                                                       \
      printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);             \
      failures++;                                                        \
    }                                                                    \
  } while (0)

struct FakeBackend : Backend {
  int st = ICC_INACTIVE;
  bool statusBroken = false;
  uint8_t lastApdu[16] = {0};
  size_t lastLen = 0;
  int status() override { return statusBroken ? BE_ERROR : st; }
  int powerOn(uint8_t* atr, size_t cap, size_t* len) override {
    if (st == ICC_ABSENT) return BE_ABSENT;
    st = ICC_ACTIVE;
    *len = contactlessAtr(atr, cap);
    return BE_OK;
  }
  void powerOff() override {
    if (st == ICC_ACTIVE) st = ICC_INACTIVE;
  }
  int transmit(const uint8_t* apdu, size_t len, uint8_t* resp, size_t cap, size_t* respLen) override {
    lastLen = len < sizeof(lastApdu) ? len : sizeof(lastApdu);
    memcpy(lastApdu, apdu, lastLen);
    (void)cap;
    resp[0] = 0x01; resp[1] = 0x02; resp[2] = 0x90; resp[3] = 0x00;
    *respLen = 4;
    return BE_OK;
  }
};

static size_t msg(uint8_t type, uint8_t seq, const uint8_t* payload, size_t plen, uint8_t slot,
                  uint8_t* out) {
  memset(out, 0, 10);
  out[0] = type; out[1] = plen & 0xFF; out[2] = plen >> 8; out[5] = slot; out[6] = seq;
  if (plen) memcpy(out + 10, payload, plen);
  return 10 + plen;
}

int main() {
  uint8_t in[64], out[300];

  // ATR matches the PC/SC part 3 ATR for Ultralight-family tags
  uint8_t atr[32];
  size_t atrLen = contactlessAtr(atr, sizeof(atr));
  static const uint8_t expect[20] = {0x3b, 0x8f, 0x80, 0x01, 0x80, 0x4f, 0x0c, 0xa0, 0x00, 0x00,
                                     0x03, 0x06, 0x03, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x68};
  CHECK(atrLen == 20 && memcmp(atr, expect, 20) == 0);
  uint8_t x = 0;
  for (size_t i = 1; i < atrLen; i++) x ^= atr[i];
  CHECK(x == 0);

  // ATR from the ATS of a DESFire EV1 (TL=6, T0=75, TA, TB, TC, one historical byte 0x80)
  {
    static const uint8_t ats[6] = {0x06, 0x75, 0x77, 0x81, 0x02, 0x80};
    static const uint8_t want[6] = {0x3B, 0x81, 0x80, 0x01, 0x80, 0x80};
    uint8_t a[32];
    size_t n = atrFromAts(ats, sizeof(ats), a, sizeof(a));
    CHECK(n == 6 && memcmp(a, want, 6) == 0);
    static const uint8_t bare[2] = {0x02, 0x00};  // no TA/TB/TC, no historical bytes
    static const uint8_t wantBare[5] = {0x3B, 0x80, 0x80, 0x01, 0x01};
    n = atrFromAts(bare, sizeof(bare), a, sizeof(a));
    CHECK(n == 5 && memcmp(a, wantBare, 5) == 0);
    CHECK(atrFromAts(ats, 1, a, sizeof(a)) == 0);
    CHECK(atrFromAts(ats, sizeof(ats), a, 4) == 0);
  }

  FakeBackend be;
  Slot slot(be);

  // GetSlotStatus reports each ICC state
  for (int st : {ICC_ACTIVE, ICC_INACTIVE, ICC_ABSENT}) {
    be.st = st;
    size_t n = slot.handle(in, msg(GET_SLOT_STATUS, 9, nullptr, 0, 0, in), out, sizeof(out));
    CHECK(n == 10 && out[0] == SLOT_STATUS && out[6] == 9 && out[7] == st && out[8] == 0);
  }

  // PowerOn returns the ATR and activates
  be.st = ICC_INACTIVE;
  size_t n = slot.handle(in, msg(ICC_POWER_ON, 3, nullptr, 0, 0, in), out, sizeof(out));
  CHECK(n == 10 + 20 && out[0] == DATA_BLOCK && out[6] == 3 && out[7] == ICC_ACTIVE && out[1] == 20);
  CHECK(memcmp(out + 10, expect, 20) == 0);

  // PowerOn without a card fails with ICC mute
  be.st = ICC_ABSENT;
  n = slot.handle(in, msg(ICC_POWER_ON, 1, nullptr, 0, 0, in), out, sizeof(out));
  CHECK(n == 10 && out[0] == DATA_BLOCK && out[7] == (CMD_FAILED | ICC_ABSENT) && out[8] == ERR_ICC_MUTE);

  // XfrBlock passes the APDU through and returns the data
  be.st = ICC_ACTIVE;
  static const uint8_t apdu[5] = {0xFF, 0xCA, 0x00, 0x00, 0x00};
  n = slot.handle(in, msg(XFR_BLOCK, 5, apdu, 5, 0, in), out, sizeof(out));
  CHECK(be.lastLen == 5 && memcmp(be.lastApdu, apdu, 5) == 0);
  CHECK(n == 14 && out[0] == DATA_BLOCK && out[1] == 4 && out[7] == ICC_ACTIVE);
  CHECK(out[10] == 1 && out[11] == 2 && out[12] == 0x90 && out[13] == 0x00);

  // XfrBlock with the card gone fails
  be.st = ICC_ABSENT;
  n = slot.handle(in, msg(XFR_BLOCK, 5, apdu, 5, 0, in), out, sizeof(out));
  CHECK(n == 10 && out[7] == (CMD_FAILED | ICC_ABSENT) && out[8] == ERR_ICC_MUTE);

  // Unknown command and wrong slot
  n = slot.handle(in, msg(0x7F, 1, nullptr, 0, 0, in), out, sizeof(out));
  CHECK(n == 10 && (out[7] & CMD_FAILED) && out[8] == ERR_CMD_NOT_SUPPORTED);
  n = slot.handle(in, msg(GET_SLOT_STATUS, 1, nullptr, 0, 3, in), out, sizeof(out));
  CHECK(n == 10 && out[5] == 3 && (out[7] & CMD_FAILED) && out[8] == ERR_SLOT);

  // A chip error becomes a hardware error, not a crash
  be.statusBroken = true;
  n = slot.handle(in, msg(GET_SLOT_STATUS, 1, nullptr, 0, 0, in), out, sizeof(out));
  CHECK(n == 10 && (out[7] & CMD_FAILED) && out[8] == ERR_HW);
  be.statusBroken = false;

  // GetParameters returns a 7-byte T=1 block
  n = slot.handle(in, msg(GET_PARAMETERS, 1, nullptr, 0, 0, in), out, sizeof(out));
  CHECK(n == 17 && out[0] == PARAMETERS && out[9] == 1 && out[1] == 7);

  // Short message ignored; reply buffer too small gives no reply
  CHECK(slot.handle(in, 2, out, sizeof(out)) == 0);
  be.st = ICC_INACTIVE;
  CHECK(slot.handle(in, msg(ICC_POWER_ON, 1, nullptr, 0, 0, in), out, 12) == 0);

  // Slot change notification format
  uint8_t note[2];
  slotChangeMessage(true, true, note);
  CHECK(note[0] == 0x50 && note[1] == 0x03);
  slotChangeMessage(false, true, note);
  CHECK(note[1] == 0x02);

  if (failures) { printf("%d check(s) failed\n", failures); return 1; }
  printf("all CCID protocol checks passed\n");
  return 0;
}
