#include "Ntag215.h"

#include <string.h>

namespace nfc {

namespace {
const uint8_t CC_NTAG215[4] = {0xE1, 0x10, 0x3E, 0x00};
}

int Ntag215::readPage(uint8_t page, uint8_t out[PAGE_SIZE]) {
  uint8_t buf[16];
  int rc = t2Read(rf_, page, buf);
  if (rc != OK) return rc;
  memcpy(out, buf, PAGE_SIZE);
  return OK;
}

int Ntag215::dump(uint8_t out[DUMP_BYTES]) {
  uint8_t buf[16];
  size_t o = 0;
  for (uint8_t page = 0; page < 132; page += 4) {  // pages 0..131
    int rc = t2Read(rf_, page, buf);
    if (rc != OK) return rc;
    memcpy(out + o, buf, 16);
    o += 16;
  }
  int rc = t2Read(rf_, 131, buf);  // pages 131..134; 131 is already in the dump
  if (rc != OK) return rc;
  memcpy(out + o, buf + PAGE_SIZE, 12);  // pages 132..134
  return OK;
}

int Ntag215::isNtag215(bool* result) {
  uint8_t cc[PAGE_SIZE];
  int rc = readPage(PAGE_CC, cc);
  if (rc != OK) return rc;
  *result = memcmp(cc, CC_NTAG215, PAGE_SIZE) == 0;
  return OK;
}

int Ntag215::writePage(uint8_t page, const uint8_t data[PAGE_SIZE], bool force, bool verify) {
  if (page < 2 || page >= TOTAL_PAGES) return ERR_PROTECTED;
  if ((page < USER_FIRST || page > USER_LAST) && !force) return ERR_PROTECTED;
  int rc = t2Write(rf_, page, data);
  if (rc != OK) return rc;
  if (verify && page != PAGE_PWD && page != PAGE_PACK && page != 2 && page != 130) {
    uint8_t got[PAGE_SIZE];
    rc = readPage(page, got);
    if (rc != OK) return rc;
    if (memcmp(got, data, PAGE_SIZE) != 0) return ERR_VERIFY;
  }
  return OK;
}

int Ntag215::identCmd(Ident which, uint8_t addr, uint8_t* out, size_t* len) {
  const uint8_t tx[2] = {(uint8_t)which, addr};
  const size_t txLen = which == Ident::Version ? 1 : 2;
  const size_t want = which == Ident::Version ? 8 : which == Ident::Signature ? 32 : 3;
  uint8_t buf[34 + 2];  // reply plus the CRC_A the chip leaves in the FIFO
  size_t n = 0;
  int rc = rf_.transceive(tx, txLen, buf, sizeof(buf), &n, /*txCrc=*/true, /*rxCrc=*/true, /*timeoutMs=*/5);
  if (rc == OK && n == want) {
    memcpy(out, buf, n);
    *len = n;
    return OK;
  }
  if (rc == OK) rc = ERR_BAD_RESPONSE;
  if (rc == ERR_TIMEOUT || rc == ERR_FRAME || rc == ERR_BAD_RESPONSE) {
    Card c;
    if (selectCard(rf_, c) == OK) return ERR_REFUSED;  // still there: it refused the command
  }
  return rc;
}

int Ntag215::readUserMemory(uint8_t out[USER_READ_BYTES]) {
  size_t o = 0;
  for (uint8_t page = USER_FIRST; page <= USER_LAST; page += 4) {  // 4, 8, .. 128
    int rc = t2Read(rf_, page, out + o);
    if (rc != OK) return rc;
    o += 16;
  }
  return OK;
}

int Ntag215::readNdef(uint8_t user[USER_READ_BYTES], ndef::Record* recs, size_t maxRecs) {
  int rc = readUserMemory(user);
  if (rc != OK) return rc;
  const uint8_t* msg;
  size_t msgLen;
  if (!ndef::findMessage(user, USER_BYTES, &msg, &msgLen) || msgLen == 0) return 0;
  int n = ndef::decodeMessage(msg, msgLen, recs, maxRecs);
  return n < 0 ? ERR_NDEF : n;
}

int Ntag215::writeNdef(const ndef::Record* recs, size_t n, size_t* written) {
  uint8_t cc[PAGE_SIZE];
  int rc = readPage(PAGE_CC, cc);
  if (rc != OK) return rc;
  if (cc[0] != 0xE1) return ERR_NO_CC;

  uint8_t msg[USER_BYTES];
  size_t msgLen = ndef::encodeMessage(recs, n, msg, sizeof(msg));
  if (msgLen == 0) return ERR_TOO_LARGE;
  uint8_t blob[USER_BYTES + PAGE_SIZE];
  size_t blobLen = ndef::wrapTlv(msg, msgLen, blob, USER_BYTES);
  if (blobLen == 0) return ERR_TOO_LARGE;
  while (blobLen % PAGE_SIZE) blob[blobLen++] = 0;  // pad to whole pages

  uint8_t current[USER_READ_BYTES];
  rc = readUserMemory(current);
  if (rc != OK) return rc;
  size_t count = 0;
  for (size_t i = 0; i < blobLen; i += PAGE_SIZE) {
    if (memcmp(current + i, blob + i, PAGE_SIZE) != 0) {  // skip identical pages to save wear
      rc = writePage(USER_FIRST + i / PAGE_SIZE, blob + i);
      if (rc != OK) return rc;
      count++;
    }
  }
  *written = count;
  return OK;
}

}  // namespace nfc
