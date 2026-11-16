#include <stdio.h>
#include <string.h>

#include <vector>

#include "../src/ApduApps.h"
#include "../src/IsoDepTarget.h"

static int fails = 0;
#define CHECK(c)                                          \
  do {                                                    \
    if (!(c)) {                                           \
      printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); \
      fails++;                                            \
    }                                                     \
  } while (0)

static std::vector<uint8_t> tx(nfc::Target& t, std::vector<uint8_t> f, nfc::TargetReply* rep = nullptr) {
  uint8_t out[300];
  nfc::TargetReply r = t.handle(f.data(), f.size(), out, sizeof(out));
  if (rep) *rep = r;
  return std::vector<uint8_t>(out, out + r.len);
}
using V = std::vector<uint8_t>;

int main() {
  // ---- Type 4 NDEF tag through the ISO-DEP layer
  nfc::Type4NdefApp app;
  nfc::IsoDepTarget t(app);

  CHECK(tx(t, {0x02, 0x00, 0xA4, 0x04, 0x00}).empty());  // before RATS: silent
  V ats = tx(t, {0xE0, 0x80});
  CHECK(ats == V({0x05, 0x78, 0x80, 0x80, 0x00}));
  CHECK(t.activated());

  // SELECT the NDEF application, block number 0
  V r = tx(t, {0x02, 0x00, 0xA4, 0x04, 0x00, 0x07, 0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01, 0x00});
  CHECK(r == V({0x02, 0x90, 0x00}));
  // SELECT the CC file, block number 1: the reply mirrors it
  r = tx(t, {0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0xE1, 0x03});
  CHECK(r == V({0x03, 0x90, 0x00}));
  // READ BINARY of the CC
  r = tx(t, {0x02, 0x00, 0xB0, 0x00, 0x00, 0x0F});
  CHECK(r.size() == 1 + 15 + 2 && r[0] == 0x02 && r[1] == 0x00 && r[2] == 0x0F && r[3] == 0x20);
  CHECK(r[r.size() - 2] == 0x90);
  // SELECT the NDEF file, read NLEN
  r = tx(t, {0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0xE1, 0x04});
  CHECK(r == V({0x03, 0x90, 0x00}));
  r = tx(t, {0x02, 0x00, 0xB0, 0x00, 0x00, 0x02});
  CHECK(r == V({0x02, 0x00, 0x00, 0x90, 0x00}));

  // UPDATE BINARY: NLEN = 0 first, then the data, then the real NLEN (the usual order)
  r = tx(t, {0x03, 0x00, 0xD6, 0x00, 0x00, 0x02, 0x00, 0x00});
  CHECK(r == V({0x03, 0x90, 0x00}));
  r = tx(t, {0x02, 0x00, 0xD6, 0x00, 0x02, 0x05, 0xD1, 0x01, 0x01, 0x54, 0x00});
  CHECK(r == V({0x02, 0x90, 0x00}));
  r = tx(t, {0x03, 0x00, 0xD6, 0x00, 0x00, 0x02, 0x00, 0x05});
  CHECK(r == V({0x03, 0x90, 0x00}));
  CHECK(app.ndefFile()[1] == 0x05 && app.ndefFile()[2] == 0xD1 && app.updateCount() == 3);

  // an R(NAK) repeats the last I-block; an R(NAK) with a fresh block number just acknowledges
  r = tx(t, {0xB3});
  CHECK(r == V({0x03, 0x90, 0x00}));
  r = tx(t, {0xB2});
  CHECK(r == V({0xA2}));

  // chaining: two I-blocks with the chain bit set, then the last one
  nfc::Type4NdefApp app2;
  nfc::IsoDepTarget t2(app2);
  tx(t2, {0xE0, 0x80});
  tx(t2, {0x02, 0x00, 0xA4, 0x04, 0x00, 0x07, 0xD2, 0x76, 0x00, 0x00, 0x85, 0x01, 0x01});
  tx(t2, {0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0xE1, 0x04});
  r = tx(t2, {0x12, 0x00, 0xD6, 0x00, 0x00});  // first part of a 4-byte UPDATE BINARY
  CHECK(r == V({0xA2}));
  r = tx(t2, {0x13, 0x04, 0xAA, 0xBB});
  CHECK(r == V({0xA3}));
  r = tx(t2, {0x02, 0xCC, 0xDD});
  CHECK(r == V({0x02, 0x90, 0x00}));
  CHECK(app2.ndefFile()[0] == 0xAA && app2.ndefFile()[3] == 0xDD);

  // errors: unknown file, wrong class, unknown instruction
  r = tx(t2, {0x03, 0x00, 0xA4, 0x00, 0x0C, 0x02, 0x3F, 0x00});
  CHECK(r == V({0x03, 0x6A, 0x82}));
  r = tx(t2, {0x02, 0x80, 0x00, 0x00, 0x00});
  CHECK(r == V({0x02, 0x6E, 0x00}));
  r = tx(t2, {0x03, 0x00, 0x88, 0x00, 0x00});
  CHECK(r == V({0x03, 0x6D, 0x00}));

  // DESELECT answers and halts; afterwards the layer is silent until the next RATS
  nfc::TargetReply rep;
  r = tx(t2, {0xC2}, &rep);
  CHECK(r == V({0xC2}) && rep.halt && !t2.activated());
  CHECK(tx(t2, {0x02, 0x00, 0xB0, 0x00, 0x00, 0x02}).empty());
  CHECK(tx(t2, {0xE0, 0x80}) == ats);

  // ---- sniffer: records everything, 90 00 to all, busy flag for F0 DE
  nfc::SniffApp sniff;
  nfc::IsoDepTarget t3(sniff);
  tx(t3, {0xE0, 0x80});
  r = tx(t3, {0x02, 0xF0, 0xD2, 0x00, 0x00, 0x03, 0x11, 0x22, 0x33});
  CHECK(r == V({0x02, 0x90, 0x00}));
  r = tx(t3, {0x03, 0xF0, 0xDE, 0x00, 0x00, 0x01});
  CHECK(r == V({0x03, 0x00, 0x90, 0x00}));
  CHECK(sniff.apduCount() == 2 && sniff.sessionCount() == 1);
  const uint8_t* cap = sniff.capture();
  CHECK(cap[0] == 1 && cap[2] == 8 && cap[3] == 0xF0 && cap[3 + 8] == 2 && cap[3 + 8 + 2] == 2);

  if (fails == 0) printf("all ISO-DEP target checks passed\n");
  return fails ? 1 : 0;
}
