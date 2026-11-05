#!/usr/bin/env python3
"""Build the job board feed from the posting and watchlist files.

Standard library only. Steps, in order:

  1. Validate every posting and watchlist file. Files with errors are marked
     and moved to _errors/; repaired files in _errors/ are restored.
  2. Sort files into the folder their fields call for (git mv when possible).
  3. Find duplicates and merge them into one feed entry.
  4. Build the feed: open postings whose deadline hasn't passed and that
     aren't stale, newest first.
  5. Safety stop: if too many postings would leave the feed unexpectedly,
     write nothing and fail (unless --force).
  6. Write postings.csv, postings.json and a Markdown log.

Everything specific to one board lives in config/board.json.
Run with --help for options.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^[A-Za-z0-9_\-.~:@+]+$")
TRACKING_PARAMS = {"gh_src", "ref", "trk", "trackingid", "refid", "src"}
COMPARE_FIELDS = ["title", "company", "level", "term", "location", "region",
                  "pay", "deadline", "url", "note"]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def parse_date(value):
    """Return a date for a real YYYY-MM-DD string, else None."""
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def norm_text(value):
    value = (value or "").casefold().replace("&", " and ")
    value = re.sub(r"[^\w]+", " ", value)
    return " ".join(value.split())


def norm_url(url):
    """Normalize a URL for duplicate checks. Returns (key, is_specific)."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip().lower(), True
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    query.sort()
    q = urlencode(query)
    key = host + path + ("?" + q if q else "")
    # A URL that names one job usually has a number in its path or a query.
    specific = bool(q) or any(re.search(r"\d", seg) for seg in path.split("/"))
    return key, specific


def url_host(url):
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def md(value):
    """Make a value safe for a Markdown table cell."""
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    return text.strip()


def md_link(text, url):
    if not url:
        return ""
    safe = str(url).replace(" ", "%20").replace(")", "%29").replace("|", "%7C")
    return f"[{md(text).replace('[', '(').replace(']', ')')}]({safe})"


def dump_json(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

class Board:
    def __init__(self, root: Path):
        self.root = root
        with open(root / "config" / "board.json", encoding="utf-8") as fh:
            self.cfg = json.load(fh)
        c = self.cfg
        self.tz = ZoneInfo(c["timeZone"])
        self.tz_label = c.get("timeZoneLabel", "CT")
        self.programs = c["programs"]
        self.program_order = c.get("programOrder", list(self.programs))
        self.multi_folder = c["multiProgramFolder"]
        self.paths = c["paths"]
        self.thresholds = c["thresholds"]

    def program_label(self, programs):
        for combo in self.cfg.get("comboLabels", []):
            if all(p in programs for p in combo["requires"]):
                return combo["label"]
        return self.programs[programs[0]]["label"]

    def program_folder(self, programs):
        if len(programs) == 1:
            return self.programs[programs[0]]["folder"]
        return self.multi_folder

    def target_dir(self, kind, data):
        """Folder (relative to root) a valid record belongs in."""
        folder = self.program_folder(data["programs"])
        if kind == "posting":
            base = Path(self.paths["postings"])
            if data["status"] in self.cfg["archivedPostingStatuses"]:
                return base / self.paths["postingsArchive"] / folder
            return base / folder
        base = Path(self.paths["watchlist"])
        if data["status"] in self.cfg["inactiveWatchlistStatuses"]:
            return base / self.paths["watchlistInactive"] / folder
        return base / folder

    def errors_dir(self, kind):
        top = self.paths["postings"] if kind == "posting" else self.paths["watchlist"]
        return Path(top) / self.paths["errors"]


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------

class Git:
    def __init__(self, root: Path):
        self.root = root
        self.ok = self._run("rev-parse", "--is-inside-work-tree", check=False).strip() == "true"
        self._tracked = None

    def _run(self, *args, check=True, input_text=None):
        try:
            res = subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                                 text=True, input=input_text, encoding="utf-8")
        except FileNotFoundError:
            return ""
        if check and res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
        return res.stdout if res.returncode == 0 else ""

    def tracked(self):
        if self._tracked is None:
            self._tracked = set(self._run("ls-files", "-z", check=False).split("\0")) if self.ok else set()
        return self._tracked

    def rev_exists(self, rev):
        return self.ok and bool(self._run("rev-parse", "--verify", "--quiet", rev + "^{commit}", check=False).strip())

    def commit_times(self, *paths):
        """Map path -> unix time of the last commit that touched it."""
        out = {}
        if not self.ok:
            return out
        log = self._run("log", "--format=%x00%ct", "--name-only", "--no-renames", "--", *paths, check=False)
        current = None
        for line in log.splitlines():
            if line.startswith("\0"):
                current = int(line[1:])
            elif line.strip() and current is not None and line not in out:
                out[line] = current
        return out

    def dirty(self, *paths):
        if not self.ok:
            return set()
        out = set()
        for line in self._run("status", "--porcelain", "-z", "--untracked-files=all", "--", *paths,
                              check=False).split("\0"):
            if len(line) > 3:
                out.add(line[3:])
        return out

    def files_at(self, rev, *paths):
        """Return {path: text} for files under paths at rev."""
        if not self.rev_exists(rev):
            return {}
        names = [n for n in self._run("ls-tree", "-r", "-z", "--name-only", rev, "--", *paths,
                                      check=False).split("\0") if n.endswith(".json")]
        if not names:
            return {}
        res = subprocess.run(["git", "cat-file", "--batch"], cwd=self.root, capture_output=True,
                             input="".join(f"{rev}:{n}\n" for n in names).encode("utf-8"))
        data, out, pos = res.stdout, {}, 0
        for name in names:
            nl = data.index(b"\n", pos)
            header = data[pos:nl].decode("utf-8").split()
            pos = nl + 1
            if len(header) < 3 or header[1] == "missing":
                continue
            size = int(header[2])
            out[name] = data[pos:pos + size].decode("utf-8", errors="replace")
            pos += size + 1
        return out

    def move(self, src: Path, dst: Path):
        (self.root / dst).parent.mkdir(parents=True, exist_ok=True)
        if self.ok and src.as_posix() in self.tracked():
            self._run("mv", src.as_posix(), dst.as_posix())
            self._tracked.discard(src.as_posix())
            self._tracked.add(dst.as_posix())
        else:
            os.replace(self.root / src, self.root / dst)


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

class Record:
    def __init__(self, kind, path: Path, text):
        self.kind = kind            # "posting" or "watchlist"
        self.path = path            # relative to root
        self.text = text
        self.data = None
        self.broken = None          # parse error message
        self.errors = []
        self.warnings = []          # (problem, fix)
        self.target = path
        self.changed = False        # data edited by the build
        self.notes = []             # actions taken, for the log
        self.restored = False
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                self.data = obj
            else:
                self.broken = "top level is not a JSON object"
        except json.JSONDecodeError as exc:
            self.broken = f"broken JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"

    @property
    def id(self):
        if self.data and isinstance(self.data.get("id"), str) and self.data["id"]:
            return self.data["id"]
        return self.path.stem

    @property
    def valid(self):
        return self.broken is None and not self.errors

    @property
    def in_errors_dir(self):
        return "_errors" in self.path.parts


def scan(board: Board, kind):
    top = board.root / (board.paths["postings"] if kind == "posting" else board.paths["watchlist"])
    records = []
    if not top.exists():
        return records
    for p in sorted(top.rglob("*.json")):
        rel = p.relative_to(board.root)
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            rec = Record(kind, rel, "")
            rec.broken = f"file is not UTF-8: {exc}"
            records.append(rec)
            continue
        records.append(Record(kind, rel, text))
    return records


def blank(value):
    return not isinstance(value, str) or not value.strip()


def validate(board: Board, rec: Record):
    if rec.broken:
        return
    d, c, errs = rec.data, board.cfg, rec.errors
    if blank(d.get("id")) or not ID_RE.match(str(d.get("id"))):
        errs.append("missing or invalid id")
    progs = d.get("programs")
    if not isinstance(progs, list) or not progs:
        errs.append("programs is missing or empty")
    else:
        bad = [p for p in progs if p not in board.programs]
        if bad:
            errs.append(f"program not allowed: {', '.join(map(str, bad))}")
        elif len(set(progs)) != len(progs):
            errs.append("programs lists the same program twice")
    if rec.kind == "posting":
        for f in ("title", "company", "url"):
            if blank(d.get(f)):
                errs.append(f"missing {f}")
        statuses = c["postingStatuses"]
        if d.get("level") not in c["levels"]:
            errs.append(f"level not allowed: {d.get('level')!r}")
        if d.get("region") not in c["regions"]:
            errs.append(f"region not allowed: {d.get('region')!r}")
        dl = d.get("deadline", "")
        if dl not in ("", None) and parse_date(dl) is None:
            errs.append(f"deadline is not a real YYYY-MM-DD date: {dl!r}")
        if not blank(d.get("url")) and not str(d.get("url")).startswith("http"):
            errs.append(f"url doesn't start with http: {d.get('url')!r}")
        if parse_date(d.get("firstSeen")) is None:
            errs.append(f"firstSeen is not a real YYYY-MM-DD date: {d.get('firstSeen')!r}")
    else:
        if blank(d.get("company")):
            errs.append("missing company")
        statuses = c["watchlistStatuses"]
        if d.get("method") not in c["watchlistMethods"]:
            errs.append(f"method not allowed: {d.get('method')!r}")
    status = d.get("status")
    if status not in statuses:
        errs.append(f"status not allowed: {status!r}")
    elif status == "error":
        prev = d.get("previousStatus") or ""
        if errs:
            pass  # still broken; stays an error
        elif not prev:
            errs.append('status is "error" and there is no previousStatus to restore; set status by hand')
        elif prev not in statuses or prev == "error":
            errs.append(f"previousStatus not allowed: {prev!r}")


def plan_record(board: Board, rec: Record):
    """Decide edits and the target path for one record (nothing is written)."""
    err_dir = board.errors_dir(rec.kind)
    if rec.broken:
        rec.target = err_dir / rec.path.name
        if rec.target != rec.path:
            rec.notes.append(f"moved to {rec.target.as_posix()}")
        return
    d = rec.data
    if rec.errors:
        detail = "; ".join(rec.errors)
        if d.get("status") != "error":
            d["previousStatus"] = d.get("status", "") if isinstance(d.get("status"), str) else ""
            d["status"] = "error"
            rec.changed = True
        if d.get("errorDetail") != detail:
            d["errorDetail"] = detail
            rec.changed = True
        name = (rec.id if ID_RE.match(rec.id) else rec.path.stem) + ".json"
        rec.target = err_dir / name
        if rec.target != rec.path:
            rec.notes.append(f"moved to {rec.target.as_posix()}")
        return
    # Valid record.
    if d.get("status") == "error":
        d["status"] = d["previousStatus"]
        rec.restored = True
        rec.changed = True
    if d.get("previousStatus") or d.get("errorDetail"):
        d["previousStatus"] = ""
        d["errorDetail"] = ""
        rec.changed = True
        rec.restored = True
    target = board.target_dir(rec.kind, d) / f"{d['id']}.json"
    if rec.path.stem != d["id"]:
        rec.warnings.append(("filename doesn't match id", f"renamed to {d['id']}.json"))
    rec.target = target
    if rec.restored:
        rec.notes.append(f"repaired; restored status {d['status']!r} and moved to {target.as_posix()}")
    elif target.parent != rec.path.parent:
        rec.notes.append(f"in the wrong folder; moved to {target.as_posix()}")


def resolve_collisions(records):
    """Two files can't end up at the same path. The one already there stays."""
    by_target = {}
    for rec in records:
        by_target.setdefault(rec.target, []).append(rec)
    for target, group in by_target.items():
        if len(group) < 2:
            continue
        keeper = next((r for r in group if r.path == target), group[0])
        for rec in group:
            if rec is keeper:
                continue
            rec.target = rec.path
            rec.notes = [n for n in rec.notes if "moved to" not in n and "renamed" not in n]
            rec.warnings = [w for w in rec.warnings if w[0] != "filename doesn't match id"]
            rec.warnings.append((f"another file already uses {target.as_posix()}",
                                 "left in place; fix by hand"))


# --------------------------------------------------------------------------
# Duplicates
# --------------------------------------------------------------------------

def dup_keys(d):
    keys = [("id", d["id"])]
    key, specific = norm_url(d.get("url"))
    if key:
        keys.append(("url", key) if specific else ("url", key, norm_text(d.get("title"))))
    keys.append(("cti", norm_text(d.get("company")), norm_text(d.get("title")), norm_text(d.get("term"))))
    return keys


def group_duplicates(recs):
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, rec in enumerate(recs):
        for k in dup_keys(rec.data):
            if k in seen:
                a, b = find(i), find(seen[k])
                if a != b:
                    parent[a] = b
            else:
                seen[k] = i
    groups = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(recs[i])
    return [g for g in groups.values() if len(g) > 1]


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

class Build:
    def __init__(self, root: Path, kind: str, today: dt.date | None = None, base: str = "HEAD",
                 force: bool = False, dry_run: bool = False, now: dt.datetime | None = None):
        self.board = Board(root)
        self.root = root
        if kind not in self.board.cfg["logKinds"]:
            raise SystemExit(f"--kind must be one of {self.board.cfg['logKinds']}")
        self.kind = kind
        self.now = now or dt.datetime.now(self.board.tz)
        self.today = today or self.now.date()
        self.base = base
        self.force = force
        self.dry_run = dry_run
        self.git = Git(root)
        self.stopped = False
        self.stop_reason = ""
        self.unexpected = []
        self.expected_removals = []
        self.merges = []
        self.pending = None
        self.pending_error = None

    # -- feed rules -------------------------------------------------------
    def is_stale(self, d):
        hosts = self.board.thresholds["staleHosts"]
        host = url_host(d.get("url"))
        if not any(host == h or host.endswith("." + h) for h in hosts):
            return False
        if d.get("deadline"):
            return False
        dates = [x for x in (parse_date(d.get("firstSeen")), parse_date(d.get("lastUpdated"))) if x]
        if not dates:
            return False
        return (self.today - max(dates)).days > self.board.thresholds["staleDays"]

    def deadline_passed(self, d):
        dl = parse_date(d.get("deadline"))
        return dl is not None and dl < self.today

    def feed_state(self, d):
        """Why a valid posting is or isn't in the feed."""
        if d["status"] != "open":
            return d["status"]
        if self.deadline_passed(d):
            return "deadline passed"
        if self.is_stale(d):
            return "stale"
        return "in feed"

    # -- main ---------------------------------------------------------------
    def run(self):
        b = self.board
        self.postings = scan(b, "posting")
        self.watchlist = scan(b, "watchlist")
        all_recs = self.postings + self.watchlist
        for rec in all_recs:
            validate(b, rec)
            plan_record(b, rec)
        resolve_collisions(self.postings)
        resolve_collisions(self.watchlist)

        valid_posts = [r for r in self.postings if r.valid]

        # Duplicate warnings across all valid postings.
        for group in group_duplicates(valid_posts):
            ids = sorted(r.id for r in group)
            for rec in group:
                others = [i for i in ids if i != rec.id] or ["(same id in another file)"]
                rec.warnings.append((f"possible duplicate of {', '.join(others)}",
                                     "merged in the feed if both are listed; otherwise none"))

        # Stale warnings.
        for rec in valid_posts:
            if rec.data["status"] == "open" and self.is_stale(rec.data):
                rec.warnings.append(("stale (LinkedIn link, no deadline, over "
                                     f"{b.thresholds['staleDays']} days old)",
                                     "left open; kept out of the feed"))
            if rec.data["status"] == "open":
                missing = [f for f in ("pay", "term") if blank(rec.data.get(f))]
                if missing:
                    rec.warnings.append((f"blank {' and '.join(missing)}", "none needed"))

        # Feed candidates, with duplicates merged.
        eligible = [r for r in valid_posts if self.feed_state(r.data) == "in feed"]
        times = self.git.commit_times(b.paths["postings"])
        dirty = self.git.dirty(b.paths["postings"])
        now_ts = int(self.now.timestamp()) + 1

        def committed(rec):
            p = rec.path.as_posix()
            if p in dirty or p not in times:
                return now_ts
            return times[p]

        merged_away = {}
        entries = []
        grouped = group_duplicates(eligible)
        in_group = {id(r) for g in grouped for r in g}
        for rec in eligible:
            if id(rec) not in in_group:
                entries.append((rec, list(rec.data["programs"])))
        for group in grouped:
            group.sort(key=lambda r: (committed(r), r.data.get("lastUpdated") or "", r.id), reverse=True)
            win = group[0]
            progs = list(win.data["programs"])
            for r in group[1:]:
                for p in r.data["programs"]:
                    if p not in progs:
                        progs.append(p)
                merged_away[r.id] = win.id
            disagreements = []
            for f in COMPARE_FIELDS:
                vals = {r.id: r.data.get(f, "") for r in group}
                if len(set(map(str, vals.values()))) > 1:
                    disagreements.append((f, vals))
            self.merges.append({"winner": win, "members": group, "programs": progs,
                                "disagreements": disagreements})
            entries.append((win, progs))

        entries.sort(key=lambda e: (-parse_date(e[0].data["firstSeen"]).toordinal(),
                                    (e[0].data.get("company") or "").casefold(), e[0].id))
        self.feed = []
        for rec, progs in entries:
            row = dict(rec.data)
            row["program"] = b.program_label(progs)
            self.feed.append(row)
        self.feed_ids = [r["id"] for r in self.feed]
        self.merged_away = merged_away

        self.safety_check(valid_posts)
        self.load_pending()
        self.compute_diff()

        wrote_files = False
        if not self.stopped and not self.dry_run:
            wrote_files = self.apply(all_recs)
            feed_changed = self.write_feed()
        else:
            feed_changed = self.feed_differs()
        self.something_changed = bool(wrote_files or feed_changed or self.added or self.changes
                                      or self.closed or any(r.errors or r.broken for r in all_recs))
        self.log_path = None
        if not self.dry_run and (self.kind in b.cfg["alwaysLogKinds"] or self.something_changed
                                 or self.stopped):
            self.log_path = self.write_log()
        self.write_result()
        return 0 if not self.stopped else 1

    # -- safety stop ---------------------------------------------------------
    def previous_feed(self):
        path = self.root / self.board.paths["feedJson"]
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [row["id"] for row in data if isinstance(row, dict) and "id" in row]
        except (json.JSONDecodeError, TypeError):
            return []

    def safety_check(self, valid_posts):
        prev = self.previous_feed()
        self.previous_ids = prev
        now_ids = set(self.feed_ids)
        by_id = {}
        for r in self.postings:
            if r.data is not None:
                by_id.setdefault(r.id, []).append(r)
        for pid in prev:
            if pid in now_ids:
                continue
            recs = [r for r in by_id.get(pid, []) if r.data is not None]
            rec = next((r for r in recs if r.valid), recs[0] if recs else None)
            if rec is None:
                self.unexpected.append((pid, "file is missing or unreadable"))
                continue
            d = rec.data
            if self.deadline_passed(d):
                self.expected_removals.append((pid, "deadline passed"))
            elif rec.valid and d.get("status") == "open" and self.is_stale(d):
                self.expected_removals.append((pid, "stale"))
            elif not rec.valid:
                self.unexpected.append((pid, "file has errors: " + "; ".join(rec.errors or [rec.broken or ""])))
            elif pid in self.merged_away:
                self.unexpected.append((pid, f"merged as a duplicate into {self.merged_away[pid]}"))
            else:
                reason = d.get("closedReason") or ""
                self.unexpected.append((pid, f"status is {d.get('status')}" + (f" ({reason})" if reason else "")))
        limit = self.board.thresholds["safetyStopFraction"]
        if prev and len(self.unexpected) / len(prev) > limit:
            msg = (f"{len(self.unexpected)} of {len(prev)} postings in the previous feed would leave it "
                   f"unexpectedly ({len(self.unexpected) / len(prev):.0%}, limit {limit:.0%}).")
            if self.force:
                self.stop_reason = msg + " Overridden with --force."
            else:
                self.stopped = True
                self.stop_reason = msg + " No feed files were written and no files were moved."

    # -- pending file from the routine ---------------------------------------
    def load_pending(self):
        if self.kind != "collection":
            return
        path = self.root / self.board.paths["pending"]
        if not path.exists():
            self.pending_error = f"{self.board.paths['pending']} was not found"
            return
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                raise ValueError("top level is not a JSON object")
            self.pending = obj
        except (json.JSONDecodeError, ValueError) as exc:
            self.pending_error = f"{self.board.paths['pending']} could not be read: {exc}"

    # -- comparison with the base commit -------------------------------------
    def compute_diff(self):
        b = self.board
        base_posts = {}
        for path, text in self.git.files_at(self.base, b.paths["postings"]).items():
            try:
                d = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and isinstance(d.get("id"), str):
                base_posts[d["id"]] = d
        self.base_found = bool(base_posts) or self.git.rev_exists(self.base)
        self.added, self.changes, self.closed = [], [], []
        for rec in self.postings:
            if rec.data is None or not rec.valid:
                continue
            d = rec.data
            old = base_posts.get(d["id"])
            if old is None:
                self.added.append(rec)
                continue
            diffs = []
            for f in sorted(set(old) | set(d)):
                if f in ("statusHistory",):
                    continue
                a, c = old.get(f, ""), d.get(f, "")
                if a != c:
                    fmt = lambda v: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                    diffs.append((f, fmt(a), fmt(c)))
            if not diffs:
                continue
            if old.get("status") != "closed" and d.get("status") == "closed":
                self.closed.append(rec)
            self.changes.append((rec, diffs))

    # -- writing --------------------------------------------------------------
    def apply(self, recs):
        wrote = False
        for rec in recs:
            if rec.changed and rec.data is not None:
                (self.root / rec.path).write_text(dump_json(rec.data), encoding="utf-8")
                wrote = True
        for rec in recs:
            if rec.target != rec.path:
                if (self.root / rec.target).exists():
                    rec.warnings.append((f"{rec.target.as_posix()} already exists", "left in place; fix by hand"))
                    rec.target = rec.path
                    continue
                self.git.move(rec.path, rec.target)
                wrote = True
        return wrote

    def feed_texts(self):
        cfg = self.board.cfg["feed"]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow([h for h, _ in cfg["csvColumns"]])
        for row in self.feed:
            w.writerow([row.get(f, "") or "" for _, f in cfg["csvColumns"]])
        rows = [{k: (row.get(f, "") or "") for k, f in cfg["jsonKeys"]} for row in self.feed]
        js = json.dumps(rows, indent=cfg.get("jsonIndent", 1), ensure_ascii=False)
        return buf.getvalue(), js

    def feed_differs(self):
        csv_text, js_text = self.feed_texts()
        for name, text in ((self.board.paths["feedCsv"], csv_text), (self.board.paths["feedJson"], js_text)):
            p = self.root / name
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                return True
        return False

    def write_feed(self):
        csv_text, js_text = self.feed_texts()
        changed = False
        for name, text in ((self.board.paths["feedCsv"], csv_text), (self.board.paths["feedJson"], js_text)):
            p = self.root / name
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                p.write_text(text, encoding="utf-8")
                changed = True
        return changed

    # -- log ------------------------------------------------------------------
    def problems(self):
        rows = []
        for rec in self.postings + self.watchlist:
            f = rec.target.as_posix()
            if rec.broken:
                rows.append(("Error", f, rec.broken, "left out of the feed; moved to _errors/ (fix the JSON by hand)"))
            for e in rec.errors:
                rows.append(("Error", f, e, "left out; status set to error and moved to _errors/"))
            for note in rec.notes:
                if not rec.errors and not rec.broken:
                    rows.append(("Moved", rec.path.as_posix(), note.split(";")[0], note))
            for prob, fix in rec.warnings:
                rows.append(("Warning", f, prob, fix))
        return rows

    def write_log(self):
        b = self.board
        lines = []
        stamp = self.now.astimezone(b.tz)
        title_kind = {"collection": "Daily collection", "edit": "Edit build", "daily": "Daily build",
                      "import": "Import"}.get(self.kind, self.kind)
        lines.append(f"# {title_kind}: {stamp:%Y-%m-%d %H:%M} {b.tz_label}")
        lines.append("")
        if self.stopped:
            lines.append(f"> **Safety stop.** {self.stop_reason}")
            lines.append("")
        elif self.stop_reason:
            lines.append(f"> **Note.** {self.stop_reason}")
            lines.append("")

        problems = self.problems()
        n_err = sum(1 for p in problems if p[0] == "Error")
        n_warn = sum(1 for p in problems if p[0] == "Warning")
        n_stale = sum(1 for r in self.postings if r.valid and r.data["status"] == "open" and self.is_stale(r.data))
        wl_fail = [w for w in (self.pending or {}).get("watchlist", []) if isinstance(w, dict)
                   and str(w.get("status", "")).lower() not in ("ok", "")]
        changed_only = [c for c in self.changes if c[0] not in self.closed]

        lines += ["## 1. Summary", "",
                  "| Item | Count |", "|---|---:|",
                  f"| Added | {len(self.added)} |",
                  f"| Changed | {len(changed_only)} |",
                  f"| Closed | {len(self.closed)} |",
                  f"| Errors | {n_err} |",
                  f"| Warnings | {n_warn} |",
                  f"| Stale | {n_stale} |",
                  f"| In feed{' (not written)' if self.stopped else ''} | {len(self.feed)} |",
                  f"| Watchlist failures | {len(wl_fail)} |", ""]
        prev_n = len(self.previous_ids)
        lines.append(f"Previous feed: {prev_n} postings. Left the feed as expected: "
                     f"{len(self.expected_removals)}. Left unexpectedly: {len(self.unexpected)}. "
                     f"Compared with `{self.base}`" + ("" if self.base_found else " (not found, so every file counts as new)") + ".")
        lines.append("")
        if self.unexpected:
            lines += ["| Posting | Why it left the feed |", "|---|---|"]
            lines += [f"| {md(i)} | {md(why)} |" for i, why in self.unexpected]
            lines.append("")

        lines += ["## 2. New jobs", ""]
        if self.added:
            lines += ["| Program | Company | Title | Source | Link |", "|---|---|---|---|---|"]
            for rec in sorted(self.added, key=lambda r: (b.program_label(r.data["programs"]),
                                                         r.data["company"].casefold())):
                d = rec.data
                lines.append(f"| {md(b.program_label(d['programs']))} | {md(d['company'])} | {md(d['title'])} | "
                             f"{md(d.get('source'))} | {md_link('link', d.get('url'))} |")
        else:
            lines.append("None.")
        lines.append("")

        lines += ["## 3. Changes to existing jobs", ""]
        if self.changes:
            lines += ["| Posting | Field | Old value | New value |", "|---|---|---|---|"]
            for rec, diffs in self.changes:
                for f, a, c in diffs:
                    lines.append(f"| {md(rec.id)} | {md(f)} | {md(a)} | {md(c)} |")
        else:
            lines.append("None.")
        lines.append("")

        lines += ["## 4. Errors and warnings", ""]
        if problems or self.merges or self.pending_error:
            lines += ["| Type | File | Problem | Fix |", "|---|---|---|---|"]
            order = {"Error": 0, "Moved": 1, "Warning": 2}
            for t, f, p, fix in sorted(problems, key=lambda x: (order.get(x[0], 9), x[1])):
                lines.append(f"| {t} | {md(f)} | {md(p)} | {md(fix)} |")
            if self.pending_error and self.kind == "collection":
                lines.append(f"| Warning | {md(b.paths['pending'])} | {md(self.pending_error)} | "
                             "sections 5 and 6 are empty |")
            lines.append("")
            if self.merges:
                lines += ["### Merged duplicates", "",
                          "| Kept in feed | Merged | Programs | Fields that disagree |", "|---|---|---|---|"]
                for m in self.merges:
                    others = ", ".join(r.id for r in m["members"][1:])
                    dis = "; ".join(f"{f}: " + " / ".join(f"{k} = {v!s}" for k, v in vals.items())
                                    for f, vals in m["disagreements"]) or "none"
                    lines.append(f"| {md(m['winner'].id)} | {md(others)} | {md(', '.join(m['programs']))} | {md(dis)} |")
                lines.append("")
        else:
            lines += ["None.", ""]

        lines += ["## 5. Watchlist", ""]
        wl = (self.pending or {}).get("watchlist")
        if isinstance(wl, list) and wl:
            names = {r.id: r.data.get("company", "") for r in self.watchlist if r.data}
            lines += [f"Entries checked: {len(wl)}.", "",
                      "| Entry | Company | Result | Postings added | Notes |", "|---|---|---|---:|---|"]
            for w in wl:
                if not isinstance(w, dict):
                    continue
                lines.append(f"| {md(w.get('id'))} | {md(names.get(w.get('id'), w.get('company', '')))} | "
                             f"{md(w.get('status'))} | {md(w.get('added', 0))} | {md(w.get('notes'))} |")
            lines.append("")
            if wl_fail:
                lines += ["### Failures", "", "| Entry | Exact error | Likely cause (a guess) |", "|---|---|---|"]
                for w in wl_fail:
                    lines.append(f"| {md(w.get('id'))} | {md(w.get('error'))} | Guess: {md(w.get('likelyCause'))} |")
                lines.append("")
        else:
            lines += ["No watchlist check in this run." if self.kind != "collection"
                      else "The routine reported no watchlist checks.", ""]

        lines += ["## 6. Skipped candidates", ""]
        sk = (self.pending or {}).get("skipped")
        if isinstance(sk, list) and sk:
            lines += ["| Company | Role | Reason | Link |", "|---|---|---|---|"]
            for s in sk:
                if isinstance(s, dict):
                    lines.append(f"| {md(s.get('company'))} | {md(s.get('title'))} | {md(s.get('reason'))} | "
                                 f"{md_link('link', s.get('url'))} |")
            lines.append("")
        else:
            lines += ["None reported." if self.kind == "collection" else "Not part of this run.", ""]
        searches = (self.pending or {}).get("searches")
        if isinstance(searches, list) and searches:
            wd = (self.pending or {}).get("weekday")
            lines += ["### Searches run", ""] + ([f"Weekday used for topics: {md(wd)}.", ""] if wd else []) + \
                     ["| Query | Mode | Notes |", "|---|---|---|"]
            for s in searches:
                if isinstance(s, dict):
                    lines.append(f"| {md(s.get('query'))} | {md(s.get('mode'))} | {md(s.get('notes', s.get('results', '')))} |")
                else:
                    lines.append(f"| {md(s)} | | |")
            lines.append("")

        lines += ["## 7. All current jobs", ""]
        current = [r for r in self.postings if r.data is not None
                   and b.paths["postingsArchive"] not in r.target.parts]
        if current:
            lines += ["| Program | Company | Title | Status | Apply by | Feed |", "|---|---|---|---|---|---|"]

            def sort_key(r):
                d = r.data
                progs = d.get("programs") if r.valid else None
                return (b.program_label(progs) if progs else "~", str(d.get("company", "")).casefold(), r.id)

            for r in sorted(current, key=sort_key):
                d = r.data
                if r.valid:
                    state = self.feed_state(d)
                    if r.id in self.merged_away:
                        state = f"merged into {self.merged_away[r.id]}"
                    label = b.program_label(d["programs"])
                else:
                    state, label = "error", ", ".join(map(str, d.get("programs") or [])) if isinstance(d.get("programs"), list) else ""
                lines.append(f"| {md(label)} | {md(d.get('company'))} | {md(d.get('title'))} | "
                             f"{md(d.get('status'))} | {md(d.get('deadline'))} | {md(state)} |")
        else:
            lines.append("None.")
        lines.append("")

        folder = self.root / b.paths["logs"] / f"{stamp:%Y}" / f"{stamp:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{stamp:%Y-%m-%d_%H%M}-{b.tz_label}_{self.kind}"
        path = folder / f"{name}.md"
        n = 2
        while path.exists():
            path = folder / f"{name}-{n}.md"
            n += 1
        path.write_text("\n".join(lines), encoding="utf-8")
        return path.relative_to(self.root)

    def write_result(self):
        problems = self.problems()
        result = {
            "ok": not self.stopped,
            "stopped": self.stopped,
            "message": self.stop_reason,
            "kind": self.kind,
            "errors": sum(1 for p in problems if p[0] == "Error"),
            "warnings": sum(1 for p in problems if p[0] == "Warning"),
            "feedTotal": len(self.feed),
            "added": len(self.added),
            "closed": len(self.closed),
            "unexpectedRemovals": len(self.unexpected),
            "logPath": self.log_path.as_posix() if self.log_path else None,
        }
        self.result = result
        if self.dry_run:
            return
        path = self.root / self.board.paths["buildResult"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", default="edit", help="collection, edit, daily or import (sets the log name)")
    ap.add_argument("--base", default="HEAD", help="git revision to compare with for the log (default HEAD)")
    ap.add_argument("--force", action="store_true",
                    help="override the safety stop. Only a person may use this.")
    ap.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    ap.add_argument("--today", help="YYYY-MM-DD; for tests only")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    today = parse_date(args.today) if args.today else None
    build = Build(Path(args.root), args.kind, today=today, base=args.base, force=args.force,
                  dry_run=args.dry_run)
    try:
        code = build.run()
    except Exception:
        traceback.print_exc()
        root = Path(args.root)
        try:
            p = root / build.board.paths["buildResult"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"ok": False, "stopped": False, "errors": 1,
                                     "message": "build crashed: " + traceback.format_exc(limit=1).strip().splitlines()[-1],
                                     "logPath": None}, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
        return 2
    r = build.result
    status = "STOPPED" if r["stopped"] else "ok"
    print(f"build {status}: feed {r['feedTotal']}, added {r['added']}, closed {r['closed']}, "
          f"errors {r['errors']}, warnings {r['warnings']}, unexpected removals {r['unexpectedRemovals']}, "
          f"log {r['logPath'] or '-'}")
    if r["message"]:
        print(r["message"])
    return code


if __name__ == "__main__":
    sys.exit(main())
