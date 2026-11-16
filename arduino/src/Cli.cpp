#include "Cli.h"

#include "Desfire.h"
#include "IsoDepRf.h"

#include <stdlib.h>
#include <string.h>

namespace nfc {

namespace {
const uint8_t kEmuUid[7] = {0x04, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
// Big buffers are static to keep them off the stack.
uint8_t userBuf[USER_READ_BYTES];
uint8_t dumpBuf[DUMP_BYTES];
uint8_t firstDump[DUMP_BYTES];

// Split a line into words; double quotes group words. Modifies `line`. Returns argc.
int tokenize(char* line, char** argv, int maxArgs) {
  int argc = 0;
  char* p = line;
  while (*p && argc < maxArgs) {
    while (*p == ' ' || *p == '\t') p++;
    if (!*p) break;
    if (*p == '"') {
      argv[argc++] = ++p;
      while (*p && *p != '"') p++;
    } else {
      argv[argc++] = p;
      while (*p && *p != ' ' && *p != '\t') p++;
    }
    if (*p) *p++ = 0;
  }
  return argc;
}

int hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

bool parseHex4(const char* s, uint8_t out[4]) {
  if (strlen(s) != 8) return false;
  for (int i = 0; i < 4; i++) {
    int hi = hexNibble(s[2 * i]), lo = hexNibble(s[2 * i + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = (hi << 4) | lo;
  }
  return true;
}
}  // namespace

Cli::Cli(Stream& io, St25r3916& rf) : io_(io), rf_(rf), tag_(rf), emuTag_(kEmuUid), t4Target_(t4App_), sniffTarget_(sniffApp_), listener_(rf, emuTag_) {}

void Cli::poll() {
  while (io_.available()) {
    char c = io_.read();
    if (c == '\r' || c == '\n') {
      if (len_ > 0) {
        line_[len_] = 0;
        len_ = 0;
        run(line_);
      }
    } else if (len_ < LINE_MAX - 1) {
      line_[len_++] = c;
    }
  }
}

void Cli::help() {
  io_.println("commands: chip | detect | dump | read-ndef | write-ndef [--text T] [--lang L] [--uri U] |");
  io_.println("          write-page <page> <8 hex> [--force] | ident | desfire | cycles [N] | emulate [S] [t2|t4|sniff] | emu-dump | help");
}

int Cli::select(Card& card) { return selectCard(rf_, card); }

void Cli::run(char* line) {
  char* argv[ARGV_MAX];
  int argc = tokenize(line, argv, ARGV_MAX);
  if (argc == 0) return;
  const char* cmd = argv[0];
  int rc;
  if (!strcmp(cmd, "chip")) rc = cmdChip();
  else if (!strcmp(cmd, "detect")) rc = cmdDetect();
  else if (!strcmp(cmd, "dump")) rc = cmdDump();
  else if (!strcmp(cmd, "read-ndef")) rc = cmdReadNdef();
  else if (!strcmp(cmd, "write-ndef")) rc = cmdWriteNdef(argc, argv);
  else if (!strcmp(cmd, "write-page")) rc = cmdWritePage(argc, argv);
  else if (!strcmp(cmd, "ident")) rc = cmdIdent();
  else if (!strcmp(cmd, "desfire")) rc = cmdDesfire();
  else if (!strcmp(cmd, "cycles")) rc = cmdCycles(argc, argv);
  else if (!strcmp(cmd, "emulate")) rc = cmdEmulate(argc, argv);
  else if (!strcmp(cmd, "emu-dump")) rc = cmdEmuDump();
  else if (!strcmp(cmd, "help")) { help(); rc = OK; }
  else { io_.printf("unknown command: %s\n", cmd); help(); rc = ERR_PARAM; }
  if (rc == OK) io_.println("ok");
  else io_.printf("error: %s\n", statusName(rc));
}

int Cli::cmdChip() {
  uint8_t type = 0, rev = 0;
  int rc = rf_.identify(&type, &rev);
  if (rc == OK) io_.printf("ST25R3916 present: type=0x%02X revision=%d\n", type, rev);
  return rc;
}

int Cli::cmdDetect() {
  Card card;
  int rc = select(card);
  if (rc != OK) return rc;
  char uid[32];
  uidToHex(card, uid, sizeof(uid));
  io_.printf("UID:  %s\n", uid);
  io_.printf("ATQA: %02X%02X  SAK: 0x%02X\n", card.atqa[0], card.atqa[1], card.sak);
  if (card.sak & 0x20) {  // SAK bit 6: the card speaks ISO 14443-4 and does not answer Type 2 READ
    io_.println("Type: ISO-DEP card (ISO 14443-4); try `desfire`");
    return OK;
  }
  bool is215 = false;
  rc = tag_.isNtag215(&is215);
  if (rc != OK) return rc;
  io_.println(is215 ? "Type: NTAG215" : "Type: not an NTAG215 (CC differs)");
  return OK;
}

int Cli::cmdDump() {
  Card card;
  int rc = select(card);
  if (rc != OK) return rc;
  rc = tag_.dump(dumpBuf);
  if (rc != OK) return rc;
  for (size_t page = 0; page < TOTAL_PAGES; page++) {
    const uint8_t* c = dumpBuf + page * PAGE_SIZE;
    char text[5];
    for (int i = 0; i < 4; i++) text[i] = (c[i] >= 32 && c[i] < 127) ? c[i] : '.';
    text[4] = 0;
    io_.printf("%3u  %02X %02X %02X %02X  %s\n", (unsigned)page, c[0], c[1], c[2], c[3], text);
  }
  return OK;
}

int Cli::cmdReadNdef() {
  Card card;
  int rc = select(card);
  if (rc != OK) return rc;
  ndef::Record recs[4];
  int n = tag_.readNdef(userBuf, recs, 4);
  if (n < 0) return n;
  if (n == 0) io_.println("no NDEF message");
  char text[120];
  for (int i = 0; i < n; i++) {
    ndef::describe(recs[i], text, sizeof(text));
    io_.println(text);
  }
  return OK;
}

int Cli::cmdWriteNdef(int argc, char** argv) {
  const char* text = nullptr;
  const char* lang = "en";
  const char* uri = nullptr;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--text") && i + 1 < argc) text = argv[++i];
    else if (!strcmp(argv[i], "--lang") && i + 1 < argc) lang = argv[++i];
    else if (!strcmp(argv[i], "--uri") && i + 1 < argc) uri = argv[++i];
    else { io_.printf("bad option: %s\n", argv[i]); return ERR_PARAM; }
  }
  if (!text && !uri) {
    io_.println("give --text and/or --uri");
    return ERR_PARAM;
  }
  static uint8_t tbuf[200], ubuf[200];
  ndef::Record recs[2];
  size_t n = 0;
  if (text && !ndef::textRecord(recs[n++], tbuf, sizeof(tbuf), text, lang)) return ERR_TOO_LARGE;
  if (uri && !ndef::uriRecord(recs[n++], ubuf, sizeof(ubuf), uri)) return ERR_TOO_LARGE;
  Card card;
  int rc = select(card);
  if (rc != OK) return rc;
  size_t written = 0;
  rc = tag_.writeNdef(recs, n, &written);
  if (rc != OK) return rc;
  io_.printf("wrote %u page(s)\n", (unsigned)written);
  return OK;
}

int Cli::cmdWritePage(int argc, char** argv) {
  bool force = false;
  const char* pageArg = nullptr;
  const char* hexArg = nullptr;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--force")) force = true;
    else if (!pageArg) pageArg = argv[i];
    else if (!hexArg) hexArg = argv[i];
    else return ERR_PARAM;
  }
  uint8_t data[4];
  if (!pageArg || !hexArg || !parseHex4(hexArg, data)) {
    io_.println("usage: write-page <page> <8 hex digits> [--force]");
    return ERR_PARAM;
  }
  int page = atoi(pageArg);
  if (page < 0 || page > 255) return ERR_PARAM;
  Card card;
  int rc = select(card);
  if (rc != OK) return rc;
  rc = tag_.writePage((uint8_t)page, data, force);
  if (rc != OK) return rc;
  io_.printf("page %d = %02X%02X%02X%02X (verified where readable)\n", page, data[0], data[1],
             data[2], data[3]);
  return OK;
}

int Cli::cmdIdent() {
  Card card;
  int rc = select(card);
  if (rc != OK) return rc;
  uint8_t v[8], sig[32], cnt[3];
  size_t n = 0;
  rc = tag_.identCmd(Ident::Version, 0, v, &n);
  if (rc != OK) return rc;
  io_.print("Version:");
  for (size_t i = 0; i < n; i++) io_.printf("%s%02X", i ? " " : " ", v[i]);
  io_.println();
  io_.printf("Type: %s\n", ntagTypeName(v, n));
  rc = tag_.identCmd(Ident::Signature, 0x00, sig, &n);
  if (rc != OK) return rc;
  io_.print("Signature: ");
  for (size_t i = 0; i < n; i++) io_.printf("%02X", sig[i]);
  io_.println();
  rc = tag_.identCmd(Ident::Counter, 0x02, cnt, &n);
  uint32_t value = 0;
  if (rc == OK && counterValue(cnt, n, &value)) io_.printf("Counter: %lu\n", (unsigned long)value);
  else if (rc == ERR_REFUSED) io_.println("Counter: not readable (NAK)");
  else return rc == OK ? ERR_BAD_RESPONSE : rc;
  return OK;
}

static void printHex(Stream& io, const uint8_t* b, size_t n, char sep) {
  for (size_t i = 0; i < n; i++) {
    if (i && sep) io.print(sep);
    io.printf("%02X", b[i]);
  }
}

// ISO-DEP plus DESFire GET_VERSION and GET_APPLICATION_IDS: what the card reveals without keys.
int Cli::cmdDesfire() {
  Card card;
  int rc = select(card);
  if (rc != OK) return rc;
  char uid[32];
  uidToHex(card, uid, sizeof(uid));
  io_.printf("UID:  %s\n", uid);
  io_.printf("ATQA: %02X%02X  SAK: 0x%02X\n", card.atqa[0], card.atqa[1], card.sak);
  if (!(card.sak & 0x20)) return ERR_REFUSED;  // not an ISO-DEP card

  RfIsoDepLink link(rf_);
  IsoDep dep(link);
  rc = dep.activate();
  if (rc != OK) return rc;
  io_.print("ATS:  ");
  printHex(io_, dep.ats(), dep.atsLen(), ' ');
  io_.println();

  uint8_t v[28], aids[126];
  size_t vn = 0, an = 0;
  uint8_t sw1 = 0, sw2 = 0, asw1 = 0, asw2 = 0;
  rc = desfireCommand(dep, 0x60, v, sizeof(v), &vn, &sw1, &sw2);
  if (rc == OK) rc = desfireCommand(dep, 0x6A, aids, sizeof(aids), &an, &asw1, &asw2);
  dep.deselect();
  if (rc != OK) return rc;
  if (vn >= 28) {
    unsigned long storage = (v[5] & 1) == 0 ? 1UL << (v[5] >> 1) : 0;
    io_.printf("Version: HW %u.%u SW %u.%u vendor 0x%02X type 0x%02X storage %lu bytes\n", v[3], v[4], v[10],
               v[11], v[0], v[1], storage);
    io_.print("Card UID: ");
    printHex(io_, v + 14, 7, ':');
    io_.printf("  production week %02X year %02X\n", v[26], v[27]);
  }
  io_.printf("GET_VERSION status: %02X %02X\n", sw1, sw2);
  io_.printf("GET_APPLICATION_IDS status: %02X %02X\n", asw1, asw2);
  for (size_t i = 0; i + 3 <= an; i += 3) io_.printf("  AID %02X %02X %02X\n", aids[i], aids[i + 1], aids[i + 2]);
  return OK;
}

int Cli::cmdCycles(int argc, char** argv) {
  int n = argc > 1 ? atoi(argv[1]) : 50;
  if (n < 1 || n > 1000) return ERR_PARAM;
  int ok = 0, mismatches = 0, errors = 0;
  int lastErr = OK;
  uint32_t total = 0, worst = 0;
  bool haveFirst = false;
  for (int i = 0; i < n; i++) {
    uint32_t t0 = millis();
    Card card;
    int rc = select(card);  // no field reset here: the retry inside selectCard handles a stuck tag
    if (rc == OK) rc = tag_.dump(dumpBuf);
    if (rc != OK) {
      errors++;
      lastErr = rc;
      continue;
    }
    if (!haveFirst) {
      memcpy(firstDump, dumpBuf, DUMP_BYTES);
      haveFirst = true;
    } else if (memcmp(firstDump, dumpBuf, DUMP_BYTES) != 0) {
      mismatches++;
      continue;
    }
    ok++;
    uint32_t dt = millis() - t0;
    total += dt;
    if (dt > worst) worst = dt;
  }
  io_.printf("%d/%d cycles OK", ok, n);
  if (ok) io_.printf("; per cycle avg %u ms, max %u ms", (unsigned)(total / ok), (unsigned)worst);
  io_.println();
  if (mismatches) io_.printf("  %d dump mismatch(es)\n", mismatches);
  if (errors) io_.printf("  %d error(s), last: %s\n", errors, statusName(lastErr));
  return ok == n ? OK : ERR_BAD_RESPONSE;
}


// Act as a tag. No serial output while it runs: a reader gives a tag about 5 ms to answer.
int Cli::cmdEmulate(int argc, char** argv) {
  int seconds = 30;
  EmuMode mode = EMU_T2;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "t2")) mode = EMU_T2;
    else if (!strcmp(argv[i], "t4")) mode = EMU_T4;
    else if (!strcmp(argv[i], "sniff")) mode = EMU_SNIFF;
    else seconds = atoi(argv[i]);
  }
  if (seconds < 1 || seconds > 600) return ERR_PARAM;
  uint8_t sak = 0x00;
  const char* what = "an NTAG215";
  if (mode == EMU_T2) {
    listener_.setTarget(emuTag_);
  } else if (mode == EMU_T4) {
    listener_.setTarget(t4Target_);
    sak = 0x20;  // ISO-DEP: the reader sends RATS
    what = "a Type 4 NDEF tag";
  } else {
    listener_.setTarget(sniffTarget_);
    sniffApp_.clear();
    sak = 0x20;
    what = "an ISO-DEP card that logs every APDU";
  }
  int rc = listener_.start(kEmuUid, 0x0044, sak);
  if (rc != OK) return rc;
  emuMode_ = mode;
  io_.printf("emulating %s, UID 04112233445566, for %d s: hold the phone to the unit (any byte ends it)\n", what,
             seconds);
  uint32_t t0 = millis();
  uint32_t spins = 0;
  while (millis() - t0 < (uint32_t)seconds * 1000) {
    listener_.poll();
    if ((++spins & 0x3F) == 0 && io_.available()) {  // any byte on the serial port ends it
      io_.read();
      break;
    }
  }
  rc = listener_.stop();
  listener_.printLog(io_);
  io_.printf("fields seen: %u, frames answered: %u", (unsigned)listener_.fieldCount(), (unsigned)listener_.frameCount());
  if (mode == EMU_T2) io_.printf(", tag writes: %u", (unsigned)emuTag_.writeCount());
  if (mode == EMU_T4) io_.printf(", NDEF file updates: %u", (unsigned)t4App_.updateCount());
  if (mode == EMU_SNIFF) io_.printf(", APDUs: %u, sessions: %u", (unsigned)sniffApp_.apduCount(), (unsigned)sniffApp_.sessionCount());
  io_.println();
  return rc;
}

// What the last `emulate` run captured: the NTAG215 memory in the format of `dump` (t2), the NDEF
// file (t4) or every APDU in full (sniff).
int Cli::cmdEmuDump() {
  if (emuMode_ == EMU_SNIFF) {
    dumpApdus();
    return OK;
  }
  const uint8_t* mem;
  size_t total;
  const uint8_t* msg = nullptr;
  size_t msgLen = 0;
  if (emuMode_ == EMU_T4) {
    const uint8_t* f = t4App_.ndefFile();
    size_t nlen = (size_t)f[0] << 8 | f[1];
    io_.printf("NDEF file: NLEN = %u, %u update(s)\n", (unsigned)nlen, (unsigned)t4App_.updateCount());
    if (nlen == 0 || nlen > Type4NdefApp::NDEF_FILE_SIZE - 2) {
      io_.println("no NDEF message");
      return OK;
    }
    for (size_t i = 0; i < nlen + 2; i += 16) {
      io_.printf("%04X ", (unsigned)i);
      for (size_t j = i; j < i + 16 && j < nlen + 2; j++) io_.printf(" %02X", f[j]);
      io_.println();
    }
    msg = f + 2;
    msgLen = nlen;
  } else {
    mem = emuTag_.memory();
    total = Type2Tag::PAGES;
    for (size_t page = 0; page < total; page++) {
      const uint8_t* c = mem + page * 4;
      char text[5];
      for (int i = 0; i < 4; i++) text[i] = (c[i] >= 32 && c[i] < 127) ? c[i] : '.';
      text[4] = 0;
      io_.printf("%3u  %02X %02X %02X %02X  %s%s\n", (unsigned)page, c[0], c[1], c[2], c[3], text,
                 emuTag_.changed(page) ? "  (written)" : "");
    }
    if (!ndef::findMessage(mem + 16, Type2Tag::BYTES - 16, &msg, &msgLen) || msgLen == 0) {
      io_.println("no NDEF message");
      return OK;
    }
  }
  ndef::Record recs[4];
  int n = ndef::decodeMessage(msg, msgLen, recs, 4);
  if (n < 0) return ERR_NDEF;
  char text[120];
  for (int i = 0; i < n; i++) {
    ndef::describe(recs[i], text, sizeof(text));
    io_.println(text);
  }
  return OK;
}

// "C> <hex>" for a command the reader sent, "R> <hex>" for our answer, one APDU per line.
void Cli::dumpApdus() {
  const uint8_t* p = sniffApp_.capture();
  const uint8_t* end = p + sniffApp_.captureSize();
  io_.printf("APDUs captured: %u in %u session(s)%s\n", (unsigned)sniffApp_.apduCount(),
             (unsigned)sniffApp_.sessionCount(), sniffApp_.full() ? " (capture buffer full, later APDUs lost)" : "");
  while (p + 3 <= end) {
    size_t len = (size_t)p[1] << 8 | p[2];
    io_.print(p[0] == 1 ? "C>" : "R>");
    for (size_t i = 0; i < len; i++) io_.printf(" %02X", p[3 + i]);
    io_.println();
    p += 3 + len;
  }
}

}  // namespace nfc
