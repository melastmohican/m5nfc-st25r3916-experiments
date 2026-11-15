#!/bin/sh
# Build and run the host tests (needs only a C++ compiler; no Zephyr, no hardware).
set -e
cd "$(dirname "$0")"
OUT=${TMPDIR:-/tmp}
${CXX:-c++} -std=c++17 -Wall -Wextra -o "$OUT/z_test_ccid_proto" test_ccid_proto.cpp ../src/CcidProto.cpp
"$OUT/z_test_ccid_proto"
${CXX:-c++} -std=c++17 -Wall -Wextra -o "$OUT/z_test_ident" test_ident.cpp
"$OUT/z_test_ident"
${CXX:-c++} -std=c++17 -Wall -Wextra -o "$OUT/z_test_isodep" test_isodep.cpp ../src/IsoDep.cpp
"$OUT/z_test_isodep"
