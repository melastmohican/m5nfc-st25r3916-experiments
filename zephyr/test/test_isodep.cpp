// ISO-DEP scenarios from ISO/IEC 14443-4 Annex B (as in embassy-rs/rnfc's tests), against a
// scripted link, plus the DESFire "additional frame" loop.
#include <stdio.h>
#include <string.h>

#include <initializer_list>
#include <string>
#include <vector>

#include "../src/Desfire.h"
#include "../src/IsoDep.h"

using Bytes = std::vector<uint8_t>;
static int failures = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

static Bytes hex(const char* s) {
  Bytes out;
  int hi = -1;
  for (; *s; s++) {
    int v = (*s >= '0' && *s <= '9') ? *s - '0' : (*s >= 'a' && *s <= 'f') ? *s - 'a' + 10 : -1;
    if (v < 0) continue;
    if (hi < 0) hi = v; else { out.push_back(hi << 4 | v); hi = -1; }
  }
  return out;
}

struct Step { Bytes tx; bool timeout; Bytes rx; };

class Script : public nfc::IsoDepLink {
 public:
  std::vector<Step> steps;
  size_t pos = 0;
  bool mismatch = false;
  Script& ok(const char* tx, const char* rx) { steps.push_back({hex(tx), false, hex(rx)}); return *this; }
  Script& timeout(const char* tx) { steps.push_back({hex(tx), true, {}}); return *this; }
  int transceive(const uint8_t* tx, size_t txLen, uint8_t* rx, size_t cap, size_t* rxLen, uint32_t) override {
    if (pos >= steps.size()) { printf("unexpected extra frame\n"); mismatch = true; return nfc::ERR_I2C; }
    const Step& s = steps[pos++];
    if (Bytes(tx, tx + txLen) != s.tx) {
      printf("unexpected tx at step %zu:", pos - 1);
      for (size_t i = 0; i < txLen; i++) printf(" %02x", tx[i]);
      printf("\n");
      mismatch = true;
      return nfc::ERR_I2C;
    }
    if (s.timeout) return nfc::ERR_TIMEOUT;
    if (s.rx.size() > cap) return nfc::ERR_PARAM;
    memcpy(rx, s.rx.data(), s.rx.size());
    *rxLen = s.rx.size();
    return nfc::OK;
  }
  void sleepMs(uint32_t) override {}
};

static const char* ATS = "06 77 77 81 02 80";

// Runs one exchange and compares the response; returns the status.
static int trx(nfc::IsoDep& d, const char* tx, const char* expectRx) {
  Bytes t = hex(tx);
  uint8_t buf[256];
  size_t n = 0;
  int rc = d.transceive(t.data(), t.size(), buf, sizeof(buf), &n);
  if (rc == nfc::OK && expectRx) CHECK(Bytes(buf, buf + n) == hex(expectRx));
  return rc;
}

static void init_case(const char* ats, size_t fsc, uint32_t sfgt, uint32_t fwt) {
  Script s;
  s.ok("e0 80", ats);
  nfc::IsoDep d(s);
  CHECK(d.activate() == nfc::OK);
  CHECK(d.fsc() == fsc);
  CHECK(d.sfgt1fc() == sfgt);
  CHECK(d.fwt1fc() == fwt);
}

int main() {
  // activation: defaults, T0 only, TB only, TA and TB
  init_case("01", 32, 256 * 16, 256 * 16 * 16);
  init_case("02 05", 64, 256 * 16, 256 * 16 * 16);
  init_case("05 67 81 02 80", 128, 8192, 1048576);
  init_case("06 77 77 81 02 80", 128, 8192, 1048576);
  {  // the real DESFire EV1 ATS: FSCI 5, SFGI 1, FWI 8
    Script s;
    s.ok("e0 80", "06 75 77 81 02 80");
    nfc::IsoDep d(s);
    CHECK(d.activate() == nfc::OK);
    CHECK(d.fsc() == 64 && d.sfgt1fc() == 8192 && d.fwt1fc() == 1048576);
    CHECK(d.atsLen() == 6 && d.ats()[5] == 0x80);
  }
  {  // no ATS at all: RATS is retried, then it fails
    Script s;
    for (int i = 0; i < 4; i++) s.timeout("e0 80");
    nfc::IsoDep d(s);
    CHECK(d.activate() == nfc::ERR_TIMEOUT);
  }
  {  // B.2.1 exchange of I-blocks
    Script s;
    s.ok("e0 80", ATS).ok("02 12 34", "02 56 78").ok("03 aa bb", "03 cc dd");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "12 34", "56 78") == nfc::OK);
    CHECK(trx(d, "aa bb", "cc dd") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // B.2.2 request for waiting time extension
    Script s;
    s.ok("e0 80", ATS).ok("02 12 34", "f2 c1").ok("f2 01", "f2 c1").ok("f2 01", "02 56 78").ok("03 aa bb", "03 cc dd");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "12 34", "56 78") == nfc::OK);
    CHECK(trx(d, "aa bb", "cc dd") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // B.2.4 scenario 4: we chain
    Script s;
    s.ok("e0 80", ATS).ok("12 00 11 22 33 44 55 66", "a2").ok("13 77 88 99 aa bb cc dd", "a3")
        .ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd");
    nfc::IsoDep d(s); d.activate(); d.setFscForTest(10);
    CHECK(trx(d, "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "cc dd") == nfc::OK);
    CHECK(trx(d, "aa bb", "cc dd") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // B.2.4 scenario 5: the card chains
    Script s;
    s.ok("e0 80", ATS).ok("02 12 34", "12 00 11 22 33 44 55 66").ok("a3", "13 77 88 99 aa bb cc dd")
        .ok("a2", "02 ee ff").ok("03 aa bb", "03 cc dd");
    nfc::IsoDep d(s); d.activate(); d.setFscForTest(10);
    CHECK(trx(d, "12 34", "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff") == nfc::OK);
    CHECK(trx(d, "aa bb", "cc dd") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // B.3.1 scenario 6: first block lost
    Script s;
    s.ok("e0 80", ATS).timeout("02 11 22").ok("b2", "a3").ok("02 11 22", "02 33 44")
        .ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(trx(d, "99 aa", "bb cc") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 7: a later block lost, card answers the NAK with R(ACK)
    Script s;
    s.ok("e0 80", ATS).ok("02 11 22", "02 33 44").timeout("03 55 66").ok("b3", "a2")
        .ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(trx(d, "99 aa", "bb cc") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 8: the NAK is answered with the response itself
    Script s;
    s.ok("e0 80", ATS).timeout("02 11 22").ok("b2", "02 33 44").ok("03 55 66", "03 77 88");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 9: NAK lost too
    Script s;
    s.ok("e0 80", ATS).timeout("02 11 22").timeout("b2").ok("b2", "02 33 44").ok("03 55 66", "03 77 88");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 10: WTX after a NAK
    Script s;
    s.ok("e0 80", ATS).timeout("02 11 22").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 11
    Script s;
    s.ok("e0 80", ATS).timeout("02 11 22").timeout("b2").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 12: WTX response lost
    Script s;
    s.ok("e0 80", ATS).ok("02 11 22", "f2 c1").timeout("f2 01").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 13
    Script s;
    s.ok("e0 80", ATS).ok("02 11 22", "f2 c1").timeout("f2 01").ok("b2", "02 33 44").ok("03 55 66", "03 77 88");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 14
    Script s;
    s.ok("e0 80", ATS).ok("02 11 22", "f2 c1").timeout("f2 01").timeout("b2").ok("b2", "02 33 44").ok("03 55 66", "03 77 88");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "11 22", "33 44") == nfc::OK);
    CHECK(trx(d, "55 66", "77 88") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // B.3.4 scenario 16: first chained block lost
    Script s;
    s.ok("e0 80", ATS).timeout("12 00 11 22 33 44 55 66").ok("b2", "a2").ok("13 77 88 99 aa bb cc dd", "a3")
        .ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd");
    nfc::IsoDep d(s); d.activate(); d.setFscForTest(10);
    CHECK(trx(d, "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "cc dd") == nfc::OK);
    CHECK(trx(d, "aa bb", "cc dd") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 17: second chained block lost
    Script s;
    s.ok("e0 80", ATS).ok("12 00 11 22 33 44 55 66", "a2").timeout("13 77 88 99 aa bb cc dd").ok("b3", "a2")
        .ok("13 77 88 99 aa bb cc dd", "a3").ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd");
    nfc::IsoDep d(s); d.activate(); d.setFscForTest(10);
    CHECK(trx(d, "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "cc dd") == nfc::OK);
    CHECK(trx(d, "aa bb", "cc dd") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenario 18
    Script s;
    s.ok("e0 80", ATS).timeout("12 00 11 22 33 44 55 66").timeout("b2").ok("b2", "a2").ok("13 77 88 99 aa bb cc dd", "a3")
        .ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd");
    nfc::IsoDep d(s); d.activate(); d.setFscForTest(10);
    CHECK(trx(d, "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "cc dd") == nfc::OK);
    CHECK(trx(d, "aa bb", "cc dd") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // scenarios 19, 20: the card chains, our ACK is lost
    Script s;
    s.ok("e0 80", ATS).ok("02 12 34", "12 00 11 22 33 44 55 66").timeout("a3").ok("a3", "13 77 88 99 aa bb cc dd")
        .ok("a2", "02 ee ff").ok("03 aa bb", "03 cc dd");
    nfc::IsoDep d(s); d.activate(); d.setFscForTest(10);
    CHECK(trx(d, "12 34", "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff") == nfc::OK);
    CHECK(trx(d, "aa bb", "cc dd") == nfc::OK);
    CHECK(!s.mismatch);
  }
  {  // a card that never answers: the 10th failure ends the exchange
    Script s;
    s.ok("e0 80", ATS).timeout("02 12 34");
    for (int i = 0; i < 9; i++) s.timeout("b2");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "12 34", nullptr) == nfc::ERR_TIMEOUT);
    CHECK(!s.mismatch && s.pos == s.steps.size());
  }
  {  // protocol errors: unknown block, ACK on the last block, bad WTX length, response too big
    Script s;
    s.ok("e0 80", ATS).ok("02 12", "c2");
    nfc::IsoDep d(s); d.activate();
    CHECK(trx(d, "12", nullptr) == nfc::ERR_PROTOCOL);
    Script s2;
    s2.ok("e0 80", ATS).ok("02 12", "f2 c1 00");
    nfc::IsoDep d2(s2); d2.activate();
    CHECK(trx(d2, "12", nullptr) == nfc::ERR_PROTOCOL);
    Script s3;
    s3.ok("e0 80", ATS).ok("02 12", "a2");
    nfc::IsoDep d3(s3); d3.activate();
    CHECK(trx(d3, "12", nullptr) == nfc::ERR_PROTOCOL);
    Script s4;
    s4.ok("e0 80", ATS).ok("02 12", "02 00 11 22 33 44");
    nfc::IsoDep d4(s4); d4.activate();
    uint8_t small[2]; size_t n = 0; const uint8_t t[1] = {0x12};
    CHECK(d4.transceive(t, 1, small, sizeof(small), &n) == nfc::ERR_PARAM);
  }
  {  // deselect
    Script s;
    s.ok("e0 80", ATS).ok("c2", "c2");
    nfc::IsoDep d(s); d.activate();
    CHECK(d.deselect() == nfc::OK);
    Script s2;
    s2.ok("e0 80", ATS).ok("c2", "02");
    nfc::IsoDep d2(s2); d2.activate();
    CHECK(d2.deselect() == nfc::ERR_PROTOCOL);
  }
  {  // DESFire GET_VERSION in three frames, as the ORCA card answers it
    Script s;
    s.ok("e0 80", "06 75 77 81 02 80")
        .ok("02 90 60 00 00 00", "02 04 01 01 01 00 18 05 91 af")
        .ok("03 90 af 00 00 00", "03 04 01 01 01 04 18 05 91 af")
        .ok("02 90 af 00 00 00", "02 04 82 81 fa e2 40 80 ba 64 95 c6 40 25 15 91 00");
    nfc::IsoDep d(s); d.activate();
    uint8_t out[40]; size_t n = 0; uint8_t sw1 = 0, sw2 = 0;
    CHECK(nfc::desfireCommand(d, 0x60, out, sizeof(out), &n, &sw1, &sw2) == nfc::OK);
    CHECK(n == 28 && sw1 == 0x91 && sw2 == 0x00);
    CHECK(out[0] == 0x04 && out[1] == 0x01 && out[5] == 0x18 && out[26] == 0x25 && out[27] == 0x15);
    CHECK(!s.mismatch);
  }
  if (failures) { printf("%d failure(s)\n", failures); return 1; }
  printf("all ISO-DEP checks passed\n");
  return 0;
}
