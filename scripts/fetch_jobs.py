#!/usr/bin/env python3
"""Read a watchlist entry's public job list and print the jobs, one per line.

Standard library only. Use this for watchlist entries whose method is a job
system with a public list: greenhouse, lever, ashby, bamboohr, smartrecruiters
or workday. It replaces fetching those lists with WebFetch, which can't send
the POST request Workday needs and summarizes long lists.

  python3 scripts/fetch_jobs.py <watchlist id>            # design-related jobs not yet on the board
  python3 scripts/fetch_jobs.py <watchlist id> --known    # also show design jobs already on the board
  python3 scripts/fetch_jobs.py <watchlist id> --all      # every job, on the board or not
  python3 scripts/fetch_jobs.py --method workday --url <sourceUrl> [--all]

Output is tab-separated: title, location, posted date (if given), URL.
The last line starts with "# " and gives the counts, or "# ERROR " and the
exact error. Exit code 0 on success, 1 on error.

Design-related means the title contains a word or phrase from config/board.json
"jobKeywords" (design, designer, CMF, UX, graphic, packaging, ...) and no word
from "jobExcludeWords" (engineer, engineering). Level
(intern, co-op, entry-level) is not filtered here: qualify each job with
config/criteria.md, and use --all if a list may hide design roles under other titles.

sourceUrl formats:
  greenhouse       https://boards-api.greenhouse.io/v1/boards/<board>/jobs
  lever            https://api.lever.co/v0/postings/<company>?mode=json
  ashby            https://api.ashbyhq.com/posting-api/job-board/<org>
  bamboohr         https://<company>.bamboohr.com/careers/list
  smartrecruiters  https://api.smartrecruiters.com/v1/companies/<company>/postings
  workday          https://<tenant>.<wdN>.myworkdayjobs.com/<site>   (the public careers site URL)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (compatible; job-board-feed/1.0)"
TIMEOUT = 30


def http(url, data=None):
    headers = {"User-Agent": UA, "Accept": "application/json"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def greenhouse(url):
    d = http(url)
    for j in d.get("jobs", []):
        yield j.get("title", ""), (j.get("location") or {}).get("name", ""), (j.get("updated_at") or "")[:10], j.get("absolute_url", "")


def lever(url):
    if "mode=json" not in url:
        url += ("&" if "?" in url else "?") + "mode=json"
    for j in http(url):
        cats = j.get("categories") or {}
        yield j.get("text", ""), cats.get("location", ""), "", j.get("hostedUrl", "")


def ashby(url):
    d = http(url)
    for j in d.get("jobs", []):
        yield j.get("title", ""), j.get("location", ""), (j.get("publishedAt") or "")[:10], j.get("jobUrl", "")


def bamboohr(url):
    d = http(url)
    base = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    for j in d.get("result", []):
        loc = j.get("location") or {}
        where = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)
        yield j.get("jobOpeningName", ""), where, "", f"{base}/careers/{j.get('id')}"


def smartrecruiters(url):
    offset = 0
    while True:
        sep = "&" if "?" in url else "?"
        d = http(f"{url}{sep}limit=100&offset={offset}")
        content = d.get("content", [])
        for j in content:
            loc = j.get("location") or {}
            where = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
            company = (j.get("company") or {}).get("identifier", "")
            yield (j.get("name", ""), where, (j.get("releasedDate") or "")[:10],
                   f"https://jobs.smartrecruiters.com/{company}/{j.get('id')}")
        offset += len(content)
        if not content or offset >= d.get("totalFound", 0):
            break


def workday(url, search="", cap=2000):
    """url is the public site, e.g. https://generalmotors.wd5.myworkdayjobs.com/Careers_GM"""
    parts = urlsplit(url)
    tenant = parts.netloc.split(".")[0]
    segs = [s for s in parts.path.split("/") if s]
    if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
        segs = segs[1:]                      # drop a locale like en-US
    if not segs:
        raise ValueError("workday sourceUrl must include the site name, e.g. .../Careers_GM")
    site = segs[0]
    api = f"{parts.scheme}://{parts.netloc}/wday/cxs/{tenant}/{site}/jobs"
    offset, total = 0, None
    while True:
        d = http(api, {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search})
        posts = d.get("jobPostings", [])
        if total is None:
            total = d.get("total", 0)
        for j in posts:
            yield (j.get("title", ""), j.get("locationsText", ""), j.get("postedOn", ""),
                   f"{parts.scheme}://{parts.netloc}/{site}{j.get('externalPath', '')}")
        offset += len(posts)
        if not posts or offset >= total or offset >= cap:
            break


METHODS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "bamboohr": bamboohr,
           "smartrecruiters": smartrecruiters, "workday": workday}


def keywords():
    cfg = json.loads((ROOT / "config" / "board.json").read_text(encoding="utf-8"))
    return ([k.lower() for k in cfg.get("jobKeywords", [])],
            [k.lower() for k in cfg.get("jobExcludeWords", [])])


def matches(title, words):
    include, exclude = words
    t = " " + re.sub(r"[^a-z0-9]+", " ", title.lower()) + " "
    return any(f" {w} " in t for w in include) and not any(f" {w} " in t for w in exclude)


def known_keys():
    """Keys for every posting already in the repo (any status), to hide jobs already on the board."""
    keys = set()
    for p in (ROOT / "postings").rglob("*.json"):
        try:
            u = json.loads(p.read_text(encoding="utf-8")).get("url") or ""
        except (ValueError, UnicodeDecodeError, AttributeError):
            continue
        keys.update(job_keys(u))
    return keys


def job_keys(url):
    """A normalized URL, plus any requisition number in it (e.g. JR-202619287, R-102996).

    Requisition numbers match across a company's different career sites, so a job
    already on the board under one site is recognized on another. Short numbers
    (under 6 digits) are only matched together with the site's host, to avoid
    clashes between companies."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return set()
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    keys = {host + path.lower()}
    for prefix, num in re.findall(r"(?<![a-z])(jr|req|r)[-_]?(\d{4,})(?!\d)", path.lower()):
        req = f"{prefix.upper()}-{num}"
        keys.add(f"req:{req}" if len(num) >= 6 else f"{host}:{req}")
    return keys


def run(method, url, show_all=False):
    if method not in METHODS:
        raise ValueError(f"method {method!r} has no public job list; use WebFetch or search")
    words = keywords()
    if method == "workday" and not show_all:
        # Workday searches server-side; ask once per keyword group to keep calls few.
        seen, rows = set(), []
        # Each search is capped at 100 results; titles are filtered below.
        for q in ("design", "intern", "co-op"):
            for row in workday(url, q, cap=100):
                if row[3] not in seen:
                    seen.add(row[3])
                    rows.append(row)
        total = len(rows)
    else:
        rows = list(METHODS[method](url))
        total = len(rows)
    shown = rows if show_all else [r for r in rows if matches(r[0], words)]
    return shown, total


FETCH_ERRORS = (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError,
                TimeoutError, ConnectionError, OSError)


def new_jobs(method, url, known=None):
    """Design-titled jobs not already on the board. Returns (rows, total, hidden)."""
    shown, total = run(method, url)
    known = known_keys() if known is None else known
    kept = [r for r in shown if not (job_keys(r[3]) & known)]
    return kept, total, len(shown) - len(kept)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entry", nargs="?", help="watchlist id")
    ap.add_argument("--method")
    ap.add_argument("--url")
    ap.add_argument("--all", action="store_true", help="print every job, not just design-related titles")
    ap.add_argument("--known", action="store_true", help="also print design jobs already on the board")
    a = ap.parse_args(argv)
    method, url = a.method, a.url
    if a.entry:
        hits = list((ROOT / "watchlist").rglob(f"{a.entry}.json"))
        if not hits:
            print(f"# ERROR no watchlist file for {a.entry}")
            return 1
        d = json.loads(hits[0].read_text(encoding="utf-8"))
        method, url = d.get("method"), d.get("sourceUrl")
    if not method or not url:
        print("# ERROR need a watchlist id, or --method and --url")
        return 1
    try:
        if a.all or a.known:
            shown, total = run(method, url, a.all)
            hidden = 0
        else:
            shown, total, hidden = new_jobs(method, url)
    except FETCH_ERRORS as exc:
        print(f"# ERROR {type(exc).__name__}: {exc}")
        return 1
    for title, where, posted, link in shown:
        print("\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in (title, where, posted, link)))
    scope = "jobs found by design searches" if method == "workday" and not a.all else "jobs in the list"
    note = f", {hidden} already on the board hidden" if hidden else ""
    print(f"# {len(shown)} shown{note}, of {total} {scope} ({method} {url})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
