#!/usr/bin/env python3
"""Print a compact index of every posting and watchlist entry.

The daily routine reads this instead of opening every file. One line per file,
tab-separated. Includes archived, inactive and error files so duplicates can
be spotted.

  python3 scripts/list_index.py              # both
  python3 scripts/list_index.py postings     # postings only
  python3 scripts/list_index.py watchlist    # watchlist only
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rows(top):
    for p in sorted((ROOT / top).rglob("*.json")):
        rel = p.relative_to(ROOT).as_posix()
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            yield rel, None
            continue
        yield rel, d


def clean(v):
    if isinstance(v, list):
        v = "+".join(map(str, v))
    return str(v if v is not None else "").replace("\t", " ").replace("\n", " ")


def main(argv):
    which = argv[1] if len(argv) > 1 else "all"
    if which in ("all", "postings"):
        cols = ["id", "status", "programs", "company", "title", "term", "deadline", "url"]
        print("# POSTINGS\t" + "\t".join(cols) + "\tfile")
        n = 0
        for rel, d in rows("postings"):
            n += 1
            print("\t".join(["P"] + [clean(d.get(c)) for c in cols] + [rel]) if d
                  else f"P\t(unreadable)\t\t\t\t\t\t\t\t{rel}")
        print(f"# {n} posting files")
    if which in ("all", "watchlist"):
        cols = ["id", "status", "method", "programs", "company", "lastChecked", "found", "sourceUrl", "careersUrl"]
        print("# WATCHLIST\t" + "\t".join(cols) + "\tfile")
        n = 0
        for rel, d in rows("watchlist"):
            n += 1
            print("\t".join(["W"] + [clean(d.get(c)) for c in cols] + [rel]) if d
                  else f"W\t(unreadable)\t\t\t\t\t\t\t\t\t{rel}")
        print(f"# {n} watchlist files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
