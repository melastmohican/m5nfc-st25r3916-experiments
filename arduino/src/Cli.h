// Serial command interface mirroring python/nfc/cli.py. One command per line:
//   chip | detect | dump | read-ndef
//   write-ndef [--text "..."] [--lang en] [--uri https://...]
//   write-page <page> <8 hex digits> [--force]
//   cycles [N]      N x (select + full dump, compared with the first dump); default 50
//   help
// Every command ends with a line "ok" or "error: <reason>", so scripts know when it is done.
#pragma once
#include <Arduino.h>

#include "Ntag215.h"
#include "St25r3916.h"

namespace nfc {

class Cli {
 public:
  Cli(Stream& io, St25r3916& rf) : io_(io), rf_(rf), tag_(rf) {}
  void poll();  // call from loop(): reads available characters, runs a command per complete line

 private:
  static constexpr size_t LINE_MAX = 200;
  static constexpr size_t ARGV_MAX = 10;

  void run(char* line);
  int cmdChip();
  int cmdDetect();
  int cmdDump();
  int cmdReadNdef();
  int cmdWriteNdef(int argc, char** argv);
  int cmdWritePage(int argc, char** argv);
  int cmdIdent();
  int cmdDesfire();
  int cmdCycles(int argc, char** argv);
  void help();
  int select(Card& card);

  Stream& io_;
  St25r3916& rf_;
  Ntag215 tag_;
  char line_[LINE_MAX];
  size_t len_ = 0;
};

}  // namespace nfc
