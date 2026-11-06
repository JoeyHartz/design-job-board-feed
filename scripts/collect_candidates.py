#!/usr/bin/env python3
"""Collect new design-titled jobs from every watchlist entry with a public job list.

Standard library only. Run by the daily maintenance Action each morning, before
the routine. For every active watchlist entry whose method is a job system
(greenhouse, lever, ashby, bamboohr, smartrecruiters, workday) it runs
fetch_jobs.py's "new jobs" check: design-titled jobs whose link or requisition
number isn't already on the board.

It writes:
  reports/candidates.json   machine-readable, for the routine
  reports/candidates.md     the same as a readable page

The routine still decides whether each job qualifies. A failed list is recorded
with its exact error, and the routine checks that entry itself.

  python3 scripts/collect_candidates.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_jobs  # noqa: E402
from build_feed import Board, md, md_link  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def entries(root: Path):
    out = []
    for p in sorted((root / "watchlist").rglob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and d.get("status") == "active" and d.get("method") in fetch_jobs.METHODS:
            out.append(d)
    return out


def check(entry, known, fetcher=None):
    fetcher = fetcher or fetch_jobs.new_jobs
    base = {"id": entry["id"], "company": entry.get("company", ""), "method": entry["method"],
            "sourceUrl": entry.get("sourceUrl", "")}
    try:
        rows, total, hidden = fetcher(entry["method"], entry.get("sourceUrl", ""), known)
    except fetch_jobs.FETCH_ERRORS as exc:
        return {**base, "status": "failed", "error": f"{type(exc).__name__}: {exc}", "total": 0,
                "hiddenKnown": 0, "jobs": []}
    jobs = [{"title": t, "location": loc, "posted": posted, "url": url} for t, loc, posted, url in rows]
    return {**base, "status": "ok", "error": "", "total": total, "hiddenKnown": hidden, "jobs": jobs}


def previous_first_listed(root: Path):
    """url -> date first listed, from the last candidates.json."""
    try:
        old = json.loads((root / "reports" / "candidates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {j["url"]: j.get("firstListed", old.get("date", "")) for e in old.get("entries", [])
            for j in e.get("jobs", []) if j.get("url")}


def collect(root: Path, today: dt.date, now: dt.datetime, fetcher=None, workers=4, known=None):
    known = fetch_jobs.known_keys() if known is None else known
    seen = previous_first_listed(root)
    todo = entries(root)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda e: check(e, known, fetcher), todo))
    for e in results:
        for j in e["jobs"]:
            j["firstListed"] = seen.get(j["url"]) or today.isoformat()
    return {"date": today.isoformat(), "generated": now.isoformat(timespec="minutes"),
            "entries": results}


def to_markdown(report, tz_label="CT"):
    entries_ = report["entries"]
    n_jobs = sum(len(e["jobs"]) for e in entries_)
    n_new = sum(1 for e in entries_ for j in e["jobs"] if j.get("firstListed") == report["date"])
    failed = [e for e in entries_ if e["status"] != "ok"]
    lines = [f"# Candidate jobs: {report['date']}", "",
             f"Generated {report['generated']} by the daily maintenance Action. "
             f"{len(entries_)} job lists checked, {len(failed)} failed, {n_jobs} design-titled jobs "
             f"not yet on the board, **{n_new} first listed today**. Titles are only a first filter: the routine "
             "qualifies each one, starting with jobs first listed since its last run.", "",
             "| Entry | Company | Result | New jobs | Already on board |", "|---|---|---|---:|---:|"]
    for e in entries_:
        result = "ok" if e["status"] == "ok" else f"failed: {e['error']}"
        lines.append(f"| {md(e['id'])} | {md(e['company'])} | {md(result)} | {len(e['jobs'])} | {e['hiddenKnown']} |")
    lines.append("")
    for e in entries_:
        if not e["jobs"]:
            continue
        lines += [f"## {md(e['company'])} ({md(e['id'])})", "",
                  "| Title | Location | Posted | First listed | Link |", "|---|---|---|---|---|"]
        for j in sorted(e["jobs"], key=lambda j: j.get("firstListed", ""), reverse=True):
            fl = j.get("firstListed", "")
            lines.append(f"| {md(j['title'])} | {md(j['location'])} | {md(j['posted'])} | "
                         f"{'**' + fl + '**' if fl == report['date'] else md(fl)} | {md_link('link', j['url'])} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root)
    board = Board(root)
    now = dt.datetime.now(board.tz)
    report = collect(root, now.date(), now)
    out = root / "reports"
    out.mkdir(exist_ok=True)
    (out / "candidates.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "candidates.md").write_text(to_markdown(report, board.tz_label) + "\n", encoding="utf-8")
    failed = [e["id"] for e in report["entries"] if e["status"] != "ok"]
    print(f"# {len(report['entries'])} job lists, {sum(len(e['jobs']) for e in report['entries'])} new jobs, "
          f"{len(failed)} failed{': ' + ', '.join(failed) if failed else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
