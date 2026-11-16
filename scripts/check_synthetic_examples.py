"""Local data-terms check: do any example strings in the committed docs/prompts occur in the gitignored transcript views?

Run from the repo root (needs views/ and targets/ built locally):  python scripts/check_synthetic_examples.py
Prints ONLY counts (no transcript text). A string that matches is either a generic command (fine) or something
copied from a transcript (replace it with a synthetic one). Reads files, writes nothing.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = [os.path.join(ROOT, p) for p in ("docs/ACTION_TAXONOMY.md", "prompts/reader_prompt.md", "prompts/judge_prompt.md")]
SEARCH_DIRS = [os.path.join(ROOT, d) for d in ("views", "targets")]
MIN_LEN = 10  # ignore very short strings


def examples() -> list[str]:
    found = set()
    for path in SOURCES:
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8").read()
        for m in re.finditer(r"`([^`\n]{%d,200})`" % MIN_LEN, text):
            found.add(m.group(1).strip())
    return sorted(found)


def main() -> int:
    files = []
    for d in SEARCH_DIRS:
        for dp, _, fn in os.walk(d):
            files += [os.path.join(dp, f) for f in fn]
    if not files:
        print("No views/ or targets/ files found. Build them first (see docs/DECISIONS.md section 10).")
        return 1
    blobs = []
    for f in files:
        try:
            blobs.append(open(f, encoding="utf-8", errors="replace").read())
        except OSError:
            pass
    ex = examples()
    hits = 0
    print(f"{len(ex)} example strings, {len(blobs)} files searched")
    for s in ex:
        n = sum(1 for b in blobs if s in b)
        if n:
            hits += 1
            print(f"{n:5d} files contain: {s!r}")
    print(f"{hits} of {len(ex)} strings occur in the transcript files. Review each: generic command = ok, otherwise replace.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
