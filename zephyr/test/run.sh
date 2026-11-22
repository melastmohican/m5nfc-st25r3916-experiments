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
${CXX:-c++} -std=c++17 -Wall -Wextra -o "$OUT/z_test_type2tag" test_type2tag.cpp ../src/Type2Tag.cpp
"$OUT/z_test_type2tag"
${CXX:-c++} -std=c++17 -Wall -Wextra -o "$OUT/z_test_isodeptarget" test_isodeptarget.cpp ../src/IsoDepTarget.cpp ../src/ApduApps.cpp
"$OUT/z_test_isodeptarget"
