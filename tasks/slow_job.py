"""A slow computation: a chain of SHA-256 hashes with a pause after each round. It prints the checksum at the end.

  python slow_job.py [ROUNDS] [SECONDS_PER_ROUND]
"""
import hashlib
import sys
import time

rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 30
pause = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
value = b"uniselector"
for n in range(1, rounds + 1):
    for _ in range(100_000):
        value = hashlib.sha256(value).digest()
    time.sleep(pause)
    print(f"round {n} of {rounds}", flush=True)
print(f"checksum: {value.hex()[:16]}", flush=True)
