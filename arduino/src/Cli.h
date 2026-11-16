// Serial command interface mirroring python/nfc/cli.py. One command per line:
//   chip | detect | dump | read-ndef
//   write-ndef [--text "..."] [--lang en] [--uri https://...]
//   write-page <page> <8 hex digits> [--force]
//   emulate [S]     act as an NFC-A tag (UID 04112233445566, NTAG215 memory) for S seconds (default 30) or
//                   until any byte arrives on the serial port,
//                   then print the event log and the memory the reader wrote; emu-dump prints it again
//   cycles [N]      N x (select + full dump, compared with the first dump); default 50
//   help
// Every command ends with a line "ok" or "error: <reason>", so scripts know when it is done.
#pragma once
#include <Arduino.h>

#include "Listener.h"
#include "Ntag215.h"
#include "Type2Tag.h"
#include "St25r3916.h"

namespace nfc {

class Cli {
 public:
  Cli(Stream& io, St25r3916& rf);
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
  int cmdEmulate(int argc, char** argv);
  int cmdEmuDump();
  void help();
  int select(Card& card);

  Stream& io_;
  St25r3916& rf_;
  Ntag215 tag_;
  Type2Tag emuTag_;
  Listener listener_;
  char line_[LINE_MAX];
  size_t len_ = 0;
};

}  // namespace nfc
