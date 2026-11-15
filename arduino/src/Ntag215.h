// NTAG215 memory model: 135 pages x 4 bytes, user memory = pages 4-129 (504 bytes).
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "Ident.h"
#include "Ndef.h"
#include "Nfca.h"
#include "St25r3916.h"

namespace nfc {

constexpr size_t PAGE_SIZE = 4;
constexpr size_t TOTAL_PAGES = 135;
constexpr size_t DUMP_BYTES = TOTAL_PAGES * PAGE_SIZE;  // 540
constexpr uint8_t USER_FIRST = 4, USER_LAST = 129;
constexpr size_t USER_BYTES = (USER_LAST - USER_FIRST + 1) * PAGE_SIZE;  // 504
constexpr size_t USER_READ_BYTES = 512;  // 32 READs of 16 bytes cover pages 4..131
constexpr uint8_t PAGE_CC = 3;
constexpr uint8_t PAGE_PWD = 133, PAGE_PACK = 134;  // always read back as zeros

class Ntag215 {
 public:
  explicit Ntag215(St25r3916& rf) : rf_(rf) {}

  int readPage(uint8_t page, uint8_t out[PAGE_SIZE]);
  // All 135 pages (540 bytes). PWD/PACK pages read as zeros.
  int dump(uint8_t out[DUMP_BYTES]);
  int isNtag215(bool* result);  // capability container is E1 10 3E 00

  // Lock/config/CC pages (outside 4..129) need force=true and their bits are one-way.
  int writePage(uint8_t page, const uint8_t data[PAGE_SIZE], bool force = false, bool verify = true);

  // GET_VERSION (8 bytes), READ_SIG (32), READ_CNT (3). `addr` is the address byte of READ_SIG (0x00)
  // and READ_CNT (0x02). If the tag does not answer, it is selected again: a tag that still answers
  // refused the command (ERR_REFUSED); otherwise the original error (no card) is returned.
  int identCmd(Ident which, uint8_t addr, uint8_t* out, size_t* len);

  // 512 bytes (pages 4..131); only the first USER_BYTES are user memory.
  int readUserMemory(uint8_t out[USER_READ_BYTES]);
  // Decode the NDEF message into recs; payload pointers point into `user` (the buffer passed in).
  // Returns the record count (0 if the tag holds no message), or a negative Status.
  int readNdef(uint8_t user[USER_READ_BYTES], ndef::Record* recs, size_t maxRecs);
  // Write an NDEF message; *written = pages actually written (identical pages are skipped).
  int writeNdef(const ndef::Record* recs, size_t n, size_t* written);

 private:
  St25r3916& rf_;
};

}  // namespace nfc
