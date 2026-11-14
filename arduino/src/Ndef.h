// Minimal NDEF encode/decode for Type 2 tags (text and URI records).
// C++ port of python/nfc/ndef.py. No heap: callers provide the buffers.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace nfc {
namespace ndef {

constexpr uint8_t TLV_NULL = 0x00;
constexpr uint8_t TLV_NDEF = 0x03;
constexpr uint8_t TLV_TERMINATOR = 0xFE;
constexpr size_t MAX_TYPE = 8;

struct Record {
  uint8_t tnf;
  uint8_t typeLen;
  uint8_t type[MAX_TYPE];
  uint16_t payloadLen;
  const uint8_t* payload;  // points into the buffer it was built from / decoded from
};

// Text record ("T"): payload = [lang length][lang][UTF-8 text]. `buf` receives the payload.
bool textRecord(Record& rec, uint8_t* buf, size_t cap, const char* text, const char* lang = "en");
// URI record ("U"): payload = [prefix code][rest of the URI]. `buf` receives the payload.
bool uriRecord(Record& rec, uint8_t* buf, size_t cap, const char* uri);

// Returns the encoded length, or 0 if `out` is too small.
size_t encodeMessage(const Record* recs, size_t n, uint8_t* out, size_t cap);
// Returns the number of records decoded (stops after the ME record), or -1 if the data is malformed.
int decodeMessage(const uint8_t* data, size_t len, Record* out, size_t maxRecs);

// NDEF TLV + terminator, as stored from page 4 of a Type 2 tag. Returns length, or 0 if too small.
size_t wrapTlv(const uint8_t* msg, size_t len, uint8_t* out, size_t cap);
// Locate the NDEF TLV in raw user memory. False if there is none.
bool findMessage(const uint8_t* user, size_t len, const uint8_t** msg, size_t* msgLen);

// "text[en]: hello", "uri: https://example.com", or "tnf=.. type=.. payload=.."
void describe(const Record& rec, char* out, size_t cap);

}  // namespace ndef
}  // namespace nfc
