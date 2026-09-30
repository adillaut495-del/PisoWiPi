"""Throwaway locator with exact character positions."""
import re
from pathlib import Path

path = Path(r"c:\PisoWifi\router\hap-ax-lite-piso.rsc")
lines = path.read_text(encoding="utf-8", newline="").split("\r\n")

line = lines[159]
print("line 160 length:", len(line))
print("repr:", repr(line))
print()
for match in re.finditer(r"\S+", line):
    print(f"{match.start() + 1:4}-{match.end():4}  {match.group()!r}")
print()
print("non-ascii characters:", [(i + 1, char, hex(ord(char))) for i, char in enumerate(line) if ord(char) > 126])
print("runs of spaces:", [(m.start() + 1, len(m.group())) for m in re.finditer(r" {2,}", line)])
print("region 195-260:", repr(line[194:260]))
print("chars 200-220:", [(i + 1, line[i]) for i in range(199, 220)])
