# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.28.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(nfc_st25r3916)

# Function-local statics in main() run once, single-threaded: no guard runtime needed.
target_compile_options(app PRIVATE $<$<COMPILE_LANGUAGE:CXX>:-fno-threadsafe-statics>)
target_sources(app PRIVATE
  src/main.cpp
  src/Io.cpp
  src/St25r3916.cpp
  src/Nfca.cpp
  src/Ntag215.cpp
  src/Ndef.cpp
  src/IsoDep.cpp
  src/Cli.cpp
  src/CcidProto.cpp
  src/NfcBackend.cpp
  src/CcidService.cpp
  src/ccid_class.c
)

# USB device setup helper from the Zephyr samples (used as a template by Zephyr itself).
include(${ZEPHYR_BASE}/samples/subsys/usb/common/common.cmake)
