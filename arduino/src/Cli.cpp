#include "Cli.h"

#include <stdlib.h>
#include <string.h>

namespace nfc {

namespace {
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
  io_.println("          write-page <page> <8 hex> [--force] | ident | cycles [N] | help");
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
  else if (!strcmp(cmd, "cycles")) rc = cmdCycles(argc, argv);
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

}  // namespace nfc
