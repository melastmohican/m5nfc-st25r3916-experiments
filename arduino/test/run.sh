#!/bin/sh
# Build and run the host tests (needs only a C++ compiler).
set -e
cd "$(dirname "$0")"
${CXX:-c++} -std=c++17 -Wall -Wextra -o /tmp/test_ccid_proto test_ccid_proto.cpp ../src/CcidProto.cpp
/tmp/test_ccid_proto
${CXX:-c++} -std=c++17 -Wall -Wextra -o /tmp/test_ident test_ident.cpp
/tmp/test_ident
