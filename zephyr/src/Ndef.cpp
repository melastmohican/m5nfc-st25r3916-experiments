#include "Ndef.h"

#include <stdio.h>
#include <string.h>

namespace nfc {
namespace ndef {

namespace {
const char* const URI_PREFIXES[] = {
    "", "http://www.", "https://www.", "http://", "https://", "tel:", "mailto:",
    "ftp://anonymous:anonymous@", "ftp://ftp.", "ftps://", "sftp://", "smb://",
    "nfs://", "ftp://", "dav://", "news:", "telnet://", "imap:", "rtsp://",
    "urn:", "pop:", "sip:", "sips:", "tftp:", "btspp://", "btl2cap://",
    "btgoep://", "tcpobex://", "irdaobex://", "file://", "urn:epc:id:",
    "urn:epc:tag:", "urn:epc:pat:", "urn:epc:raw:", "urn:epc:", "urn:nfc:",
};
constexpr size_t N_PREFIXES = sizeof(URI_PREFIXES) / sizeof(URI_PREFIXES[0]);

void initRecord(Record& rec, const char* type, const uint8_t* payload, uint16_t len) {
  rec.tnf = 1;  // NFC Forum well-known type
  rec.typeLen = (uint8_t)strlen(type);
  memcpy(rec.type, type, rec.typeLen);
  rec.payload = payload;
  rec.payloadLen = len;
}
}  // namespace

bool textRecord(Record& rec, uint8_t* buf, size_t cap, const char* text, const char* lang) {
  size_t langLen = strlen(lang), textLen = strlen(text);
  size_t total = 1 + langLen + textLen;
  if (langLen > 0x3F || total > cap || total > 0xFFFF) return false;
  buf[0] = (uint8_t)langLen;  // status byte: UTF-8, language length
  memcpy(buf + 1, lang, langLen);
  memcpy(buf + 1 + langLen, text, textLen);
  initRecord(rec, "T", buf, (uint16_t)total);
  return true;
}

bool uriRecord(Record& rec, uint8_t* buf, size_t cap, const char* uri) {
  size_t best = 0;
  for (size_t i = 1; i < N_PREFIXES; i++) {
    size_t pl = strlen(URI_PREFIXES[i]);
    if (strncmp(uri, URI_PREFIXES[i], pl) == 0 && pl > strlen(URI_PREFIXES[best])) best = i;
  }
  size_t rest = strlen(uri) - strlen(URI_PREFIXES[best]);
  size_t total = 1 + rest;
  if (total > cap || total > 0xFFFF) return false;
  buf[0] = (uint8_t)best;
  memcpy(buf + 1, uri + strlen(URI_PREFIXES[best]), rest);
  initRecord(rec, "U", buf, (uint16_t)total);
  return true;
}

size_t encodeMessage(const Record* recs, size_t n, uint8_t* out, size_t cap) {
  size_t o = 0;
  for (size_t i = 0; i < n; i++) {
    const Record& r = recs[i];
    bool shortRec = r.payloadLen < 256;
    uint8_t header = r.tnf & 0x07;
    if (i == 0) header |= 0x80;      // MB
    if (i == n - 1) header |= 0x40;  // ME
    if (shortRec) header |= 0x10;    // SR
    size_t need = 2 + (shortRec ? 1 : 4) + r.typeLen + r.payloadLen;
    if (o + need > cap) return 0;
    out[o++] = header;
    out[o++] = r.typeLen;
    if (shortRec) {
      out[o++] = (uint8_t)r.payloadLen;
    } else {
      out[o++] = 0;
      out[o++] = 0;
      out[o++] = r.payloadLen >> 8;
      out[o++] = r.payloadLen & 0xFF;
    }
    memcpy(out + o, r.type, r.typeLen);
    o += r.typeLen;
    memcpy(out + o, r.payload, r.payloadLen);
    o += r.payloadLen;
  }
  return o;
}

int decodeMessage(const uint8_t* data, size_t len, Record* out, size_t maxRecs) {
  size_t i = 0;
  int n = 0;
  while (i < len) {
    if (i + 2 > len || (size_t)n >= maxRecs) return -1;
    uint8_t header = data[i];
    bool sr = header & 0x10, il = header & 0x08;
    uint8_t typeLen = data[i + 1];
    i += 2;
    uint32_t payloadLen;
    if (sr) {
      if (i + 1 > len) return -1;
      payloadLen = data[i++];
    } else {
      if (i + 4 > len) return -1;
      payloadLen = ((uint32_t)data[i] << 24) | ((uint32_t)data[i + 1] << 16) |
                   ((uint32_t)data[i + 2] << 8) | data[i + 3];
      i += 4;
    }
    uint8_t idLen = 0;
    if (il) {
      if (i + 1 > len) return -1;
      idLen = data[i++];
    }
    if (typeLen > MAX_TYPE || i + typeLen + idLen + payloadLen > len || payloadLen > 0xFFFF)
      return -1;
    Record& r = out[n++];
    r.tnf = header & 0x07;
    r.typeLen = typeLen;
    memcpy(r.type, data + i, typeLen);
    i += typeLen + idLen;
    r.payload = data + i;
    r.payloadLen = (uint16_t)payloadLen;
    i += payloadLen;
    if (header & 0x40) break;  // ME
  }
  return n;
}

size_t wrapTlv(const uint8_t* msg, size_t len, uint8_t* out, size_t cap) {
  size_t lenBytes = len < 0xFF ? 1 : 3;
  if (1 + lenBytes + len + 1 > cap) return 0;
  size_t o = 0;
  out[o++] = TLV_NDEF;
  if (len < 0xFF) {
    out[o++] = (uint8_t)len;
  } else {
    out[o++] = 0xFF;
    out[o++] = len >> 8;
    out[o++] = len & 0xFF;
  }
  memcpy(out + o, msg, len);
  o += len;
  out[o++] = TLV_TERMINATOR;
  return o;
}

bool findMessage(const uint8_t* user, size_t len, const uint8_t** msg, size_t* msgLen) {
  size_t i = 0;
  while (i < len) {
    uint8_t tag = user[i];
    if (tag == TLV_NULL) {
      i++;
    } else if (tag == TLV_TERMINATOR) {
      return false;
    } else {
      if (i + 1 >= len) return false;
      size_t length = user[i + 1], start = i + 2;
      if (length == 0xFF) {
        if (i + 4 > len) return false;
        length = ((size_t)user[i + 2] << 8) | user[i + 3];
        start = i + 4;
      }
      if (tag == TLV_NDEF) {
        if (start + length > len) return false;
        *msg = user + start;
        *msgLen = length;
        return true;
      }
      i = start + length;
    }
  }
  return false;
}

void describe(const Record& rec, char* out, size_t cap) {
  if (rec.tnf == 1 && rec.typeLen == 1 && rec.type[0] == 'T' && rec.payloadLen > 0) {
    size_t langLen = rec.payload[0] & 0x3F;
    if (1 + langLen > rec.payloadLen) langLen = rec.payloadLen - 1;
    snprintf(out, cap, "text[%.*s]: %.*s", (int)langLen, (const char*)rec.payload + 1,
             (int)(rec.payloadLen - 1 - langLen), (const char*)rec.payload + 1 + langLen);
  } else if (rec.tnf == 1 && rec.typeLen == 1 && rec.type[0] == 'U' && rec.payloadLen > 0) {
    uint8_t code = rec.payload[0];
    snprintf(out, cap, "uri: %s%.*s", code < N_PREFIXES ? URI_PREFIXES[code] : "",
             (int)rec.payloadLen - 1, (const char*)rec.payload + 1);
  } else {
    int o = snprintf(out, cap, "tnf=%d type=", rec.tnf);
    for (uint8_t i = 0; i < rec.typeLen && o > 0 && (size_t)o < cap; i++)
      o += snprintf(out + o, cap - o, "%02x", rec.type[i]);
    if (o > 0 && (size_t)o < cap) o += snprintf(out + o, cap - o, " payload=");
    for (uint16_t i = 0; i < rec.payloadLen && o > 0 && (size_t)o + 3 < cap; i++)
      o += snprintf(out + o, cap - o, "%02x", rec.payload[i]);
  }
}

}  // namespace ndef
}  // namespace nfc
