#!/usr/bin/env python3
"""Print a compact index of every posting and watchlist entry.

The daily routine reads this instead of opening every file. One line per file,
tab-separated. Includes archived, inactive and error files so duplicates can
be spotted.

  python3 scripts/list_index.py              # postings and watchlist
  python3 scripts/list_index.py postings     # postings only
  python3 scripts/list_index.py watchlist    # watchlist only
  python3 scripts/list_index.py recheck      # open postings due for a re-check today
  python3 scripts/list_index.py searches     # the search entries to check today
"""

import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def config():
    return json.loads((ROOT / "config" / "board.json").read_text(encoding="utf-8"))


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


def host(url):
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def recheck():
    t = config()["thresholds"]
    skip = t.get("recheckSkipHosts", [])
    due = []
    for rel, d in rows("postings"):
        if not d or d.get("status") != "open" or "_errors" in rel:
            continue
        h = host(d.get("url"))
        if any(h == s or h.endswith("." + s) for s in skip):
            continue
        due.append(((d.get("lastVerified") or d.get("firstSeen") or ""), d.get("firstSeen") or "", d.get("id", ""), d, rel))
    due.sort(key=lambda x: x[:3])
    n = t.get("recheckPerRun", 25)
    cols = ["id", "lastVerified", "deadline", "company", "title", "url"]
    print("# RECHECK\t" + "\t".join(cols) + "\tfile")
    for *_, d, rel in due[:n]:
        print("\t".join(["R"] + [clean(d.get(c)) for c in cols] + [rel]))
    print(f"# {min(n, len(due))} of {len(due)} open non-LinkedIn postings, oldest lastVerified first")


def searches():
    cfg = config()
    n = cfg.get("routineChecks", {}).get("searchEntriesPerRun", 12)
    entries = [d for rel, d in rows("watchlist")
               if d and d.get("status") == "active" and d.get("method") == "search"]
    entries.sort(key=lambda d: (d.get("lastChecked") or "", d.get("id", "")))
    cols = ["id", "company", "lastChecked", "careersUrl", "segment"]
    print("# SEARCH ENTRIES\t" + "\t".join(cols))
    for d in entries[:n]:
        print("\t".join(["S"] + [clean(d.get(c)) for c in cols]))
    print(f"# {min(n, len(entries))} of {len(entries)} active search entries, oldest lastChecked first")


def main(argv):
    which = argv[1] if len(argv) > 1 else "all"
    if which == "recheck":
        recheck()
        return 0
    if which == "searches":
        searches()
        return 0
    if which in ("all", "postings"):
        cols = ["id", "status", "programs", "company", "title", "term", "deadline", "lastVerified", "url"]
        print("# POSTINGS\t" + "\t".join(cols) + "\tfile")
        n = 0
        for rel, d in rows("postings"):
            n += 1
            print("\t".join(["P"] + [clean(d.get(c)) for c in cols] + [rel]) if d
                  else "P\t(unreadable)" + "\t" * len(cols) + rel)
        print(f"# {n} posting files")
    if which in ("all", "watchlist"):
        cols = ["id", "status", "method", "programs", "company", "lastChecked", "found", "sourceUrl", "careersUrl"]
        print("# WATCHLIST\t" + "\t".join(cols) + "\tfile")
        n = 0
        for rel, d in rows("watchlist"):
            n += 1
            print("\t".join(["W"] + [clean(d.get(c)) for c in cols] + [rel]) if d
                  else "W\t(unreadable)" + "\t" * len(cols) + rel)
        print(f"# {n} watchlist files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
