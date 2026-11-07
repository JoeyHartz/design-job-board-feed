#!/usr/bin/env python3
"""Approve or reject pending postings from a GitHub issue checklist.

Standard library only. Used by the Approvals workflow. New postings from the
routine have status "pending" and stay off the feed until a person approves them.

  python3 scripts/approvals.py render
      Writes reports/approvals.md: the body of the "Jobs waiting for approval"
      issue, one entry per pending posting with an Approve and a Reject checkbox,
      and an "Approve all" checkbox at the top. Prints the number pending.

  python3 scripts/approvals.py apply <issue-body-file>
      Reads the ticked boxes from the edited issue body and updates the files:
      Approve -> status "open" (published on the next build)
      Reject  -> status "hidden" (never published; the routine won't re-add it)
      Approve all -> approves every listed posting not ticked Reject.
      Both boxes ticked on one posting -> left pending, and reported.
      Only postings that are still pending are changed. Prints a JSON summary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_feed import Board, dump_json, md, parse_date  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
ITEM_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(Approve|Reject)\b[^`\n]*`([A-Za-z0-9_\-.~:@+]+)`", re.M)
ALL_RE = re.compile(r"^\s*[-*]\s+\[[xX]\]\s+\*{0,2}Approve all\b", re.M)
MARKER = "<!-- approvals:v1 -->"


def pending(root: Path, board: Board):
    out = []
    for p in sorted((root / board.paths["postings"]).rglob("*.json")):
        if board.paths["errors"] in p.parts:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and d.get("status") == "pending":
            out.append((p, d))
    return out


def render(root: Path, today: dt.date, repo_url: str = ""):
    board = Board(root)
    items = pending(root, board)
    stale_days = board.cfg.get("approval", {}).get("staleAfterDays", 7)

    def label(d):
        progs = d.get("programs") or []
        return board.program_label(progs) if progs and all(x in board.programs for x in progs) else "?"

    items.sort(key=lambda x: (label(x[1]), x[1].get("firstSeen", ""), x[1].get("company", "").casefold()))
    lines = [MARKER,
             f"**{len(items)} job{'s' if len(items) != 1 else ''} waiting for approval.** "
             "Tick **Approve** to publish a job or **Reject** to keep it off the board for good. "
             "Changes apply about a minute after you tick; this list then refreshes. "
             "Jobs you leave unticked stay here.", ""]
    if not items:
        lines.append("Nothing is waiting right now.")
        return "\n".join(lines) + "\n", 0
    lines += ["- [ ] **Approve all** jobs listed below (except any you tick Reject)", ""]
    current = None
    for p, d in items:
        prog = label(d)
        if prog != current:
            lines += [f"## {prog}", ""]
            current = prog
        seen = parse_date(d.get("firstSeen"))
        waited = (today - seen).days if seen else 0
        flag = f" ⏳ waiting {waited} days" if waited > stale_days else ""
        facts = [x for x in (d.get("level"), d.get("term"), d.get("location"), d.get("region"),
                             d.get("pay") or "pay not stated",
                             f"apply by {d['deadline']}" if d.get("deadline") else "no deadline") if x]
        file_link = f"{repo_url}/blob/main/{p.relative_to(root).as_posix()}" if repo_url else p.relative_to(root).as_posix()
        lines += [f"### {md(d.get('company'))}: {md(d.get('title'))}{flag}", "",
                  " · ".join(md(x) for x in facts), ""]
        if d.get("note"):
            lines += [f"> {md(d['note'])}", ""]
        lines += [f"[Open the posting]({d.get('url')}) · source: {md(d.get('source'))} · found {md(d.get('firstSeen'))} · "
                  f"[file]({file_link})", "",
                  f"- [ ] Approve `{d['id']}`",
                  f"- [ ] Reject `{d['id']}`", ""]
    return "\n".join(lines) + "\n", len(items)


def parse(body: str):
    """Return (approve_ids, reject_ids, approve_all, listed_ids)."""
    approve, reject, listed = set(), set(), set()
    for mark, action, pid in ITEM_RE.findall(body or ""):
        listed.add(pid)
        if mark.lower() == "x":
            (approve if action == "Approve" else reject).add(pid)
    return approve, reject, bool(ALL_RE.search(body or "")), listed


def apply(root: Path, body: str, today: dt.date, approver: str = "a person"):
    board = Board(root)
    approve, reject, approve_all, listed = parse(body)
    if approve_all:
        approve |= listed - reject
    conflicts = approve & reject
    approve -= conflicts
    reject -= conflicts
    by_id = {d["id"]: (p, d) for p, d in pending(root, board)}
    done = {"approved": [], "rejected": [], "conflicts": sorted(conflicts), "notPending": []}
    stamp = today.isoformat()
    for pid in sorted(approve | reject):
        if pid not in by_id:
            done["notPending"].append(pid)
            continue
        p, d = by_id[pid]
        if pid in approve:
            d["status"] = "open"
            d.setdefault("statusHistory", []).append({"date": stamp, "status": "open", "reason": f"approved by {approver}"})
            done["approved"].append(pid)
        else:
            d["status"] = "hidden"
            d.setdefault("statusHistory", []).append({"date": stamp, "status": "hidden", "reason": f"rejected by {approver}"})
            done["rejected"].append(pid)
        d["lastUpdated"] = stamp
        p.write_text(dump_json(d), encoding="utf-8")
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--repo-url", default="")
    a_ = sub.add_parser("apply")
    a_.add_argument("body_file")
    a_.add_argument("--approver", default="a person")
    for s in (r, a_):
        s.add_argument("--today", help="YYYY-MM-DD; for tests only")
        s.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root)
    board = Board(root)
    today = parse_date(a.today) if a.today else dt.datetime.now(board.tz).date()
    if a.cmd == "render":
        text, n = render(root, today, a.repo_url)
        (root / "reports").mkdir(exist_ok=True)
        (root / "reports" / "approvals.md").write_text(text, encoding="utf-8")
        print(n)
    else:
        body = Path(a.body_file).read_text(encoding="utf-8")
        print(json.dumps(apply(root, body, today, a.approver)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
