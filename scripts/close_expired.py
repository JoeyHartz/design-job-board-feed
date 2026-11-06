#!/usr/bin/env python3
"""Close open postings whose deadline has passed (America/Chicago date).

Standard library only. Run by the daily maintenance Action before the build.
For each posting with status "open" and a deadline before today, it sets
status "closed", closedAt and closedReason, adds a statusHistory entry and
updates lastUpdated. Hidden postings and postings without a deadline are never
touched. The build then moves closed files to _archive/.

  python3 scripts/close_expired.py            # close and report
  python3 scripts/close_expired.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_feed import Board, dump_json, parse_date  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def close_expired(root: Path, today: dt.date, dry_run=False):
    board = Board(root)
    closed = []
    for p in sorted((root / board.paths["postings"]).rglob("*.json")):
        if board.paths["errors"] in p.parts:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(d, dict) or d.get("status") != "open":
            continue
        deadline = parse_date(d.get("deadline"))
        if deadline is None or deadline >= today:
            continue
        stamp = today.isoformat()
        d["status"] = "closed"
        d["closedAt"] = stamp
        d["closedReason"] = f"deadline passed ({deadline.isoformat()})"
        d["lastUpdated"] = stamp
        d.setdefault("statusHistory", []).append(
            {"date": stamp, "status": "closed", "reason": f"deadline passed ({deadline.isoformat()})"})
        closed.append((d.get("id", p.stem), d.get("company", ""), d.get("title", ""), deadline.isoformat()))
        if not dry_run:
            p.write_text(dump_json(d), encoding="utf-8")
    return closed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", help="YYYY-MM-DD; for tests only")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root)
    board = Board(root)
    today = parse_date(a.today) if a.today else dt.datetime.now(board.tz).date()
    closed = close_expired(root, today, a.dry_run)
    for pid, company, title, deadline in closed:
        print(f"closed\t{pid}\t{company}\t{title}\tdeadline {deadline}")
    print(f"# {len(closed)} posting(s) {'would be ' if a.dry_run else ''}closed for a passed deadline ({today.isoformat()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
