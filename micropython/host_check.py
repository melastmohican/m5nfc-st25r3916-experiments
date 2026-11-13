"""List PC/SC readers on the Mac (run with: uv run --with pyscard python host_check.py)."""
from smartcard.System import readers

found = readers()
print("readers:", found or "none")
for r in found:
    print(" -", r)
