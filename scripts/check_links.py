#!/usr/bin/env python3
"""Check every open posting's link and flag the ones that look gone.

Standard library only. Run by the daily maintenance Action. It never changes a
posting: it writes a report, and the routine confirms each flagged posting by
opening it before closing anything (some sites block automated checks).

Results for each open posting:
  ok           the page loaded
  gone         404/410, Greenhouse "?error=true", a Workday job the Workday
               data endpoint no longer knows, or a redirect from a specific job
               link to a general careers/search page
  unreachable  blocked, rate-limited, timed out or a server error: couldn't tell
  skipped      a host in thresholds.recheckSkipHosts (LinkedIn can't be read)

It writes reports/links.json and reports/links.md.

  python3 scripts/check_links.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_feed import Board, md, md_link, url_host  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (compatible; job-board-feed link check)"
TIMEOUT = 25
GENERAL_TAILS = {"", "careers", "career", "jobs", "job", "search", "search-jobs", "jobsearch", "openings",
                 "opportunities", "join-us", "work-with-us", "en", "en-us", "us"}


BLOCK_MARKERS = ("attention required", "you have been blocked", "cf-chl", "enable cookies",
                 "captcha", "access denied", "are you a robot", "unusual traffic")


def fetch(url):
    """Return (status, final_url, start_of_body). Raises on network errors."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.geturl(), resp.read(65536).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(65536).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, exc.geturl() or url, body


def workday_api(url):
    """https://t.wd5.myworkdayjobs.com/Site/job/... -> .../wday/cxs/t/Site/job/..."""
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
        segs = segs[1:]
    if len(segs) < 3 or "job" not in segs:
        return None
    tenant = parts.netloc.split(".")[0]
    return f"{parts.scheme}://{parts.netloc}/wday/cxs/{tenant}/{'/'.join(segs)}"


def looks_general(original, final):
    """True when a specific job link ended up on a general careers or search page."""
    o, f = urlsplit(original), urlsplit(final)
    if (o.netloc, o.path.rstrip("/")) == (f.netloc, f.path.rstrip("/")):
        return False
    tail = f.path.rstrip("/").split("/")[-1].lower() if f.path.strip("/") else ""
    lost_id = bool(re.search(r"\d{3,}", o.path)) and not re.search(r"\d{3,}", f.path)
    return tail in GENERAL_TAILS or lost_id


def classify(url, fetcher=fetch):
    """Return (result, detail)."""
    host = url_host(url)
    try:
        if host.endswith("myworkdayjobs.com"):
            api = workday_api(url)
            if api:
                status = fetcher(api)[0]
                if status in (404, 410):
                    return "gone", f"Workday no longer lists this job (HTTP {status})"
                if status == 200:
                    return "ok", "Workday lists this job"
        status, final, body = fetcher(url)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as exc:
        return "unreachable", f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"
    if status >= 400 and any(m in body[:20000].lower() for m in BLOCK_MARKERS):
        return "unreachable", f"HTTP {status}, blocked by the site's bot protection"
    if status in (404, 410):
        return "gone", f"HTTP {status}"
    if status >= 400:
        return "unreachable", f"HTTP {status}"
    if "error=true" in final:
        return "gone", f"redirected to {final}"
    if looks_general(url, final):
        return "gone", f"redirected to a general page: {final}"
    return "ok", f"HTTP {status}"


def open_postings(root: Path, board: Board):
    out = []
    for p in sorted((root / board.paths["postings"]).rglob("*.json")):
        if board.paths["errors"] in p.parts or board.paths["postingsArchive"] in p.parts:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict) and d.get("status") == "open" and d.get("url"):
            out.append(d)
    return out


def check_all(root: Path, now: dt.datetime, fetcher=fetch, workers=8):
    board = Board(root)
    skip = board.thresholds.get("recheckSkipHosts", [])
    posts = open_postings(root, board)

    def one(d):
        host = url_host(d["url"])
        if any(host == h or host.endswith("." + h) for h in skip):
            result, detail = "skipped", f"{host} can't be checked automatically"
        else:
            result, detail = classify(d["url"], fetcher)
        return {"id": d["id"], "company": d.get("company", ""), "title": d.get("title", ""),
                "url": d["url"], "lastVerified": d.get("lastVerified", ""), "result": result, "detail": detail}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, posts))
    return {"date": now.date().isoformat(), "generated": now.isoformat(timespec="minutes"), "postings": rows}


def to_markdown(report):
    rows = report["postings"]
    count = {k: sum(1 for r in rows if r["result"] == k) for k in ("ok", "gone", "unreachable", "skipped")}
    lines = [f"# Link check: {report['date']}", "",
             f"Generated {report['generated']} by the daily maintenance Action. {len(rows)} open postings: "
             f"{count['ok']} ok, **{count['gone']} look gone**, {count['unreachable']} couldn't be checked, "
             f"{count['skipped']} skipped (LinkedIn). Nothing is closed automatically: the routine opens each "
             "\"look gone\" posting and closes it only if it's really gone.", ""]
    for key, title in (("gone", "Look gone (routine: confirm and close)"),
                       ("unreachable", "Couldn't be checked (no action needed)")):
        sel = [r for r in rows if r["result"] == key]
        lines += [f"## {title}", ""]
        if not sel:
            lines += ["None.", ""]
            continue
        lines += ["| Posting | Company | Title | Detail | Link |", "|---|---|---|---|---|"]
        for r in sel:
            lines.append(f"| {md(r['id'])} | {md(r['company'])} | {md(r['title'])} | {md(r['detail'])} | "
                         f"{md_link('link', r['url'])} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root)
    board = Board(root)
    report = check_all(root, dt.datetime.now(board.tz))
    out = root / "reports"
    out.mkdir(exist_ok=True)
    (out / "links.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "links.md").write_text(to_markdown(report) + "\n", encoding="utf-8")
    rows = report["postings"]
    print(f"# {len(rows)} open postings: " + ", ".join(
        f"{sum(1 for r in rows if r['result'] == k)} {k}" for k in ("ok", "gone", "unreachable", "skipped")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
