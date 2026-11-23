"""X's weighted length: most Latin text counts 1 per character, CJK and emoji 2, any URL 23."""

import re

URL = re.compile(r"https?://\S+")
LIGHT = ((0x0000, 0x10FF), (0x2000, 0x200D), (0x2010, 0x201F), (0x2032, 0x2037))
LIMIT = 280


def weighted_length(t: str) -> int:
    n = 0
    for part in URL.split(t):
        n += sum(1 if any(a <= ord(c) <= b for a, b in LIGHT) else 2 for c in part)
    return n + 23 * len(URL.findall(t))
