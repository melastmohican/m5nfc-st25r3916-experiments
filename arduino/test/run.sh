#!/bin/sh
# Build and run the host tests (needs only a C++ compiler).
set -e
cd "$(dirname "$0")"
${CXX:-c++} -std=c++17 -Wall -Wextra -o /tmp/test_ccid_proto test_ccid_proto.cpp ../src/CcidProto.cpp
/tmp/test_ccid_proto
${CXX:-c++} -std=c++17 -Wall -Wextra -o /tmp/test_ident test_ident.cpp
/tmp/test_ident
${CXX:-c++} -std=c++17 -Wall -Wextra -o /tmp/test_isodep test_isodep.cpp ../src/IsoDep.cpp
/tmp/test_isodep
${CXX:-c++} -std=c++17 -Wall -Wextra -o /tmp/test_type2tag test_type2tag.cpp ../src/Type2Tag.cpp
/tmp/test_type2tag
