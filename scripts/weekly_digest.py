#!/usr/bin/env python3
"""Write a weekly summary of the board for a GitHub issue.

Standard library only. Run by the maintenance Action every Monday; the Action
opens it as an issue (GitHub emails watchers when an issue opens).

Covers the last 7 days (America/Chicago): jobs added by program, jobs closed,
jobs closing in the next 14 days, the latest link check and candidate list,
and any routine runs whose checks failed.

  python3 scripts/weekly_digest.py            # writes reports/digest.md and prints it
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_feed import Board, md, md_link, parse_date  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def load_postings(root: Path, board: Board):
    out = []
    for p in sorted((root / board.paths["postings"]).rglob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and isinstance(d.get("programs"), list) and d["programs"]:
            out.append(d)
    return out


def digest(root: Path, today: dt.date, repo_url: str = ""):
    board = Board(root)
    start = today - dt.timedelta(days=7)
    posts = load_postings(root, board)
    in_week = lambda s: (parse_date(s) is not None and start < parse_date(s) <= today)  # noqa: E731
    label = lambda d: board.program_label(d["programs"]) if all(p in board.programs for p in d["programs"]) else "?"  # noqa: E731

    added = [d for d in posts if in_week(d.get("firstSeen"))]
    closed = [d for d in posts if d.get("status") == "closed" and in_week(d.get("closedAt"))]
    try:
        feed = json.loads((root / board.paths["feedJson"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        feed = []
    horizon = today + dt.timedelta(days=board.thresholds.get("closingSoonDays", 14))
    soon = sorted((r for r in feed if parse_date(r.get("apply_by")) and today <= parse_date(r["apply_by"]) <= horizon),
                  key=lambda r: r["apply_by"])

    link = (lambda path: f"{repo_url}/blob/main/{path}") if repo_url else (lambda path: path)
    lines = [f"Week of {(start + dt.timedelta(days=1)).isoformat()} to {today.isoformat()}.", "",
             "| | Count |", "|---|---:|",
             f"| Jobs in the feed now | {len(feed)} |",
             f"| Added this week | {len(added)} |",
             f"| Closed this week | {len(closed)} |",
             f"| Closing in the next 14 days | {len(soon)} |", ""]

    by_prog = defaultdict(list)
    for d in added:
        by_prog[label(d)].append(d)
    lines += ["## Added this week", ""]
    if added:
        for prog in sorted(by_prog):
            lines += [f"**{prog}** ({len(by_prog[prog])})", ""]
            for d in sorted(by_prog[prog], key=lambda d: d.get("company", "").casefold()):
                lines.append(f"- {md_link(d.get('company', '') + ' – ' + d.get('title', ''), d.get('url'))} "
                             f"({md(d.get('location'))})")
            lines.append("")
    else:
        lines += ["None.", ""]

    lines += ["## Closed this week", ""]
    if closed:
        reasons = defaultdict(int)
        for d in closed:
            r = d.get("closedReason") or "no reason given"
            reasons[re.sub(r"\s*\(.*\)$", "", r)] += 1
        lines += [", ".join(f"{n} {r}" for r, n in sorted(reasons.items(), key=lambda x: -x[1])), ""]
    else:
        lines += ["None.", ""]

    lines += ["## Closing in the next 14 days", ""]
    if soon:
        lines += ["| Apply by | Program | Company | Title |", "|---|---|---|---|"]
        for r in soon:
            lines.append(f"| {md(r['apply_by'])} | {md(r.get('program'))} | {md(r.get('company'))} | "
                         f"{md_link(r.get('title', ''), r.get('link'))} |")
        lines.append("")
    else:
        lines += ["None.", ""]

    lines += ["## Automated checks", ""]
    try:
        lk = json.loads((root / "reports" / "links.json").read_text(encoding="utf-8"))
        rows = lk["postings"]
        gone = sum(1 for r in rows if r["result"] == "gone")
        lines.append(f"- Link check ({lk['date']}): {gone} open postings look gone and are waiting for the routine "
                     f"to confirm. [Report]({link('reports/links.md')})")
    except (OSError, ValueError, KeyError):
        lines.append("- Link check: no report yet.")
    try:
        cd = json.loads((root / "reports" / "candidates.json").read_text(encoding="utf-8"))
        failed = [e["id"] for e in cd["entries"] if e["status"] != "ok"]
        lines.append(f"- Candidate jobs ({cd['date']}): {sum(len(e['jobs']) for e in cd['entries'])} new "
                     f"design-titled jobs from {len(cd['entries'])} job lists"
                     + (f"; failed: {', '.join(failed)}" if failed else "") + f". [Report]({link('reports/candidates.md')})")
    except (OSError, ValueError, KeyError):
        lines.append("- Candidate jobs: no report yet.")

    logs = sorted((root / board.paths["logs"]).rglob("*_collection.md"))
    week_logs = [p for p in logs if (d := parse_date(p.name[:10])) and start < d <= today]
    problems = []
    for p in week_logs:
        m = re.search(r"\| Routine check problems \| (\d+) \|", p.read_text(encoding="utf-8"))
        if m and int(m.group(1)) > 0:
            problems.append((p, int(m.group(1))))
    lines.append(f"- Daily routine runs this week: {len(week_logs)}"
                 + (f"; {len(problems)} with failed routine checks: "
                    + ", ".join(f"[{p.name[:10]}]({link(p.relative_to(root).as_posix())}) ({n})" for p, n in problems)
                    if problems else "; all routine checks passed." if week_logs else "."))
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--today", help="YYYY-MM-DD; for tests only")
    ap.add_argument("--repo-url", default="", help="e.g. https://github.com/owner/repo, for links")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root)
    board = Board(root)
    today = parse_date(a.today) if a.today else dt.datetime.now(board.tz).date()
    text = digest(root, today, a.repo_url)
    (root / "reports").mkdir(exist_ok=True)
    (root / "reports" / "digest.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
