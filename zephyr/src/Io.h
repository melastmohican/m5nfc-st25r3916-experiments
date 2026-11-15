// Byte stream the CLI talks to (here: the USB CDC-ACM serial port).
#pragma once
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace nfc {

class Io {
 public:
  virtual ~Io() {}
  virtual int available() = 0;  // 1 if a byte can be read, else 0
  virtual int read() = 0;       // next byte or -1
  virtual size_t write(const uint8_t* data, size_t len) = 0;

  size_t print(const char* s) { return write((const uint8_t*)s, strlen(s)); }
  size_t print(char c) { return write((const uint8_t*)&c, 1); }
  size_t println(const char* s = "") {
    size_t n = print(s);
    return n + write((const uint8_t*)"\n", 1);
  }
  int printf(const char* fmt, ...) __attribute__((format(printf, 2, 3)));
};

}  // namespace nfc
