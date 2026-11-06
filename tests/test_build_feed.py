"""Tests for scripts/build_feed.py. Run: python3 -m unittest discover -s tests -v"""

import copy
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLES = Path(__file__).resolve().parent / "samples"
sys.path.insert(0, str(REPO / "scripts"))

import build_feed  # noqa: E402

TODAY = dt.date(2026, 11, 5)
NOW = dt.datetime(2026, 11, 5, 10, 5, tzinfo=build_feed.ZoneInfo("America/Chicago"))


def sample_posting(**overrides):
    d = json.loads((SAMPLES / "posting.json").read_text(encoding="utf-8"))
    d.update(overrides)
    return d


def sample_watch(**overrides):
    d = json.loads((SAMPLES / "watchlist.json").read_text(encoding="utf-8"))
    d.update(overrides)
    return d


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="board-test-"))
        shutil.copytree(REPO / "config", self.tmp / "config")
        (self.tmp / "postings").mkdir()
        (self.tmp / "watchlist").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers ----------------------------------------------------------------
    def put(self, rel, data):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return p

    def posting(self, folder="industrial-design", **kw):
        d = sample_posting(**kw)
        return self.put(f"postings/{folder}/{d['id']}.json", d)

    def read(self, rel):
        return json.loads((self.tmp / rel).read_text(encoding="utf-8"))

    def build(self, kind="edit", force=False, today=TODAY):
        b = build_feed.Build(self.tmp, kind, today=today, force=force, now=NOW)
        code = b.run()
        return b, code

    def feed(self):
        return json.loads((self.tmp / "postings.json").read_text(encoding="utf-8"))

    def git(self, *args, date=None):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        if date:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
        subprocess.run(["git", *args], cwd=self.tmp, check=True, capture_output=True, env=env)


class ValidationTests(BoardTest):
    def test_valid_posting_is_published_with_feed_columns(self):
        self.posting()
        b, code = self.build()
        self.assertEqual(code, 0)
        rows = self.feed()
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0].keys()),
                         ["id", "program", "title", "company", "type", "term", "location", "region",
                          "pay", "apply_by", "found", "note", "link"])
        header = (self.tmp / "postings.csv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(header, "Program,Title,Company,Type,Term,Location,Region,Pay,Apply by,Found,Note,Link")

    def test_invalid_value_moves_to_errors(self):
        self.posting(id="bad-region", region="Mars")
        b, code = self.build()
        self.assertEqual(code, 0)
        self.assertFalse((self.tmp / "postings/industrial-design/bad-region.json").exists())
        d = self.read("postings/_errors/bad-region.json")
        self.assertEqual(d["status"], "error")
        self.assertEqual(d["previousStatus"], "open")
        self.assertIn("region not allowed", d["errorDetail"])
        self.assertEqual(self.feed(), [])
        self.assertEqual(b.result["errors"], 1)

    def test_bad_deadline_and_url_are_errors(self):
        self.posting(id="bad-date", deadline="2026-02-30", url="www.example.com/x")
        self.build()
        d = self.read("postings/_errors/bad-date.json")
        self.assertIn("deadline", d["errorDetail"])
        self.assertIn("url", d["errorDetail"])

    def test_broken_json_is_only_moved(self):
        text = (SAMPLES / "broken.json").read_text(encoding="utf-8")
        self.put("postings/graphic-design/broken-sample.json", text)
        b, _ = self.build()
        moved = self.tmp / "postings/_errors/broken-sample.json"
        self.assertTrue(moved.exists())
        self.assertEqual(moved.read_text(encoding="utf-8"), text)
        self.assertEqual(b.result["errors"], 1)
        self.assertIn("broken JSON", (self.tmp / b.log_path).read_text(encoding="utf-8"))

    def test_repaired_error_file_is_restored(self):
        d = sample_posting(id="fixed-one", status="error", previousStatus="open",
                           errorDetail="region not allowed: 'Mars'")
        self.put("postings/_errors/fixed-one.json", d)
        self.build()
        self.assertFalse((self.tmp / "postings/_errors/fixed-one.json").exists())
        d = self.read("postings/industrial-design/fixed-one.json")
        self.assertEqual((d["status"], d["previousStatus"], d["errorDetail"]), ("open", "", ""))
        self.assertEqual([r["id"] for r in self.feed()], ["fixed-one"])

    def test_repaired_broken_json_keeps_its_status(self):
        # A file moved for broken JSON has no previousStatus; once fixed it keeps its status.
        self.put("postings/_errors/was-broken.json", sample_posting(id="was-broken", status="closed"))
        self.build()
        self.assertTrue((self.tmp / "postings/_archive/industrial-design/was-broken.json").exists())

    def test_error_file_still_broken_stays(self):
        d = sample_posting(id="still-bad", status="error", previousStatus="open", region="Mars",
                           errorDetail="old")
        self.put("postings/_errors/still-bad.json", d)
        self.build()
        d = self.read("postings/_errors/still-bad.json")
        self.assertEqual(d["status"], "error")
        self.assertEqual(d["previousStatus"], "open")

    def test_misplaced_file_is_moved(self):
        self.posting(folder="graphic-design", id="wrong-folder")
        b, _ = self.build()
        self.assertTrue((self.tmp / "postings/industrial-design/wrong-folder.json").exists())
        self.assertFalse((self.tmp / "postings/graphic-design/wrong-folder.json").exists())
        log = (self.tmp / b.log_path).read_text(encoding="utf-8")
        self.assertIn("wrong folder", log)

    def test_multi_program_folder(self):
        self.posting(folder="industrial-design", id="two-programs", programs=["ID", "UX"])
        self.build()
        self.assertTrue((self.tmp / "postings/multi-program/two-programs.json").exists())

    def test_filename_mismatch_is_fixed(self):
        d = sample_posting(id="real-id")
        self.put("postings/industrial-design/wrong-name.json", d)
        b, _ = self.build()
        self.assertTrue((self.tmp / "postings/industrial-design/real-id.json").exists())
        self.assertGreaterEqual(b.result["warnings"], 1)

    def test_closing_a_posting_archives_it(self):
        self.posting(id="now-closed", status="closed", closedAt="2026-11-05", closedReason="filled")
        self.build()
        self.assertTrue((self.tmp / "postings/_archive/industrial-design/now-closed.json").exists())
        self.assertEqual(self.feed(), [])

    def test_hidden_posting_is_archived_and_unpublished(self):
        self.posting(id="hidden-one", status="hidden")
        self.build()
        self.assertTrue((self.tmp / "postings/_archive/industrial-design/hidden-one.json").exists())
        self.assertEqual(self.feed(), [])


class WatchlistTests(BoardTest):
    def test_inactive_entry_moves_to_inactive(self):
        self.put("watchlist/industrial-design/sample-co.json", sample_watch(status="inactive"))
        self.build()
        self.assertTrue((self.tmp / "watchlist/_inactive/industrial-design/sample-co.json").exists())

    def test_reactivated_entry_moves_back(self):
        self.put("watchlist/_inactive/graphic-design/sample-co.json", sample_watch(programs=["GD"]))
        self.build()
        self.assertTrue((self.tmp / "watchlist/graphic-design/sample-co.json").exists())

    def test_ux_and_multi_program_watch_folders(self):
        self.put("watchlist/industrial-design/ux-co.json", sample_watch(id="ux-co", programs=["UX"]))
        self.put("watchlist/industrial-design/both-co.json", sample_watch(id="both-co", programs=["ID", "GD"]))
        self.build()
        self.assertTrue((self.tmp / "watchlist/ui-ux/ux-co.json").exists())
        self.assertTrue((self.tmp / "watchlist/multi-program/both-co.json").exists())

    def test_bad_method_is_error(self):
        self.put("watchlist/industrial-design/sample-co.json", sample_watch(method="rss"))
        self.build()
        d = self.read("watchlist/_errors/sample-co.json")
        self.assertEqual((d["status"], d["previousStatus"]), ("error", "active"))


class ProgramLabelTests(BoardTest):
    def test_labels(self):
        board = build_feed.Board(self.tmp)
        cases = {
            ("ID",): "Industrial Design",
            ("GD",): "Graphic Design",
            ("UX",): "UI/UX & Digital Product",
            ("ID", "GD"): "ID + GD",
            ("GD", "ID"): "ID + GD",
            ("ID", "GD", "UX"): "ID + GD",
            ("UX", "GD", "ID"): "ID + GD",
            ("ID", "UX"): "Industrial Design",
            ("UX", "ID"): "UI/UX & Digital Product",
            ("GD", "UX"): "Graphic Design",
            ("UX", "GD"): "UI/UX & Digital Product",
        }
        for progs, label in cases.items():
            self.assertEqual(board.program_label(list(progs)), label, progs)


class FeedRuleTests(BoardTest):
    def test_expired_deadline_is_left_out(self):
        self.posting(id="expired", title="T5", deadline="2026-11-04")
        self.posting(id="due-today", deadline="2026-11-05", url="https://example.com/jobs/2")
        self.build()
        self.assertEqual([r["id"] for r in self.feed()], ["due-today"])
        # The file is untouched: still open and in place.
        self.assertEqual(self.read("postings/industrial-design/expired.json")["status"], "open")

    def test_stale_rule(self):
        # The stale rule is about LinkedIn links, which the real config now blocks; allow them here.
        cfg_path = self.tmp / "config" / "board.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["blockedUrlHosts"] = []
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        old = {"firstSeen": "2026-09-01", "lastUpdated": "2026-09-05"}
        self.posting(id="stale-li", title="T1", url="https://www.linkedin.com/jobs/view/111/", **old)
        self.posting(id="li-with-deadline", title="T2", url="https://www.linkedin.com/jobs/view/222/",
                     deadline="2026-12-01", **old)
        self.posting(id="old-not-li", title="T3", url="https://example.com/jobs/333", **old)
        self.posting(id="li-recent", title="T4", url="https://linkedin.com/jobs/view/444/",
                     firstSeen="2026-09-01", lastUpdated="2026-10-01")
        b, _ = self.build()
        ids = {r["id"] for r in self.feed()}
        self.assertEqual(ids, {"li-with-deadline", "old-not-li", "li-recent"})
        self.assertEqual(self.read("postings/industrial-design/stale-li.json")["status"], "open")
        self.assertIn("stale", (self.tmp / b.log_path).read_text(encoding="utf-8"))

    def test_sort_order(self):
        self.posting(id="a", company="zeta", title="A", firstSeen="2026-11-01", url="https://e.com/1")
        self.posting(id="b", company="Alpha", title="B", firstSeen="2026-11-01", url="https://e.com/2")
        self.posting(id="c", company="beta", title="C", firstSeen="2026-11-03", url="https://e.com/3")
        self.build()
        self.assertEqual([r["id"] for r in self.feed()], ["c", "b", "a"])


class DuplicateTests(BoardTest):
    def test_duplicates_merge_newest_commit_wins(self):
        self.git("init", "-q")
        older = sample_posting(id="dup-old", programs=["ID"], pay="$20/hr", term="Summer 2027")
        self.put("postings/industrial-design/dup-old.json", older)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "one", date="2026-11-01T10:00:00")
        newer = sample_posting(id="dup-new", programs=["GD"], pay="$25/hr", term="Summer 2027")
        self.put("postings/graphic-design/dup-new.json", newer)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "two", date="2026-11-02T10:00:00")
        b, code = self.build()
        rows = self.feed()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "dup-new")
        self.assertEqual(rows[0]["pay"], "$25/hr")
        self.assertEqual(rows[0]["program"], "ID + GD")
        # Both files are left alone.
        self.assertTrue((self.tmp / "postings/industrial-design/dup-old.json").exists())
        self.assertEqual(self.read("postings/industrial-design/dup-old.json")["pay"], "$20/hr")
        log = (self.tmp / b.log_path).read_text(encoding="utf-8")
        self.assertIn("Merged duplicates", log)
        self.assertIn("pay:", log)

    def test_same_company_title_term_is_duplicate(self):
        self.posting(id="x1", url="https://a.com/jobs/1", title="Design Intern!")
        self.posting(id="x2", url="https://b.com/jobs/2", title="design intern")
        self.build()
        self.assertEqual(len(self.feed()), 1)

    def test_shared_careers_url_is_not_duplicate_without_title_match(self):
        self.posting(id="y1", url="https://studio.com/careers", title="Industrial Design Intern")
        self.posting(id="y2", url="https://www.studio.com/careers/", title="Graphic Design Intern",
                     programs=["GD"], folder="graphic-design")
        self.build()
        self.assertEqual(len(self.feed()), 2)

    def test_same_specific_url_is_duplicate(self):
        self.posting(id="z1", url="https://jobs.lever.co/co/abc-123?utm_source=x", title="A")
        self.posting(id="z2", url="https://jobs.lever.co/co/abc-123/", title="B", company="Other")
        self.build()
        self.assertEqual(len(self.feed()), 1)


class SafetyStopTests(BoardTest):
    def setUp(self):
        super().setUp()
        for i in range(10):
            self.posting(id=f"p{i}", url=f"https://example.com/jobs/{i}", title=f"Role {i}")
        b, _ = self.build()
        self.assertEqual(len(self.feed()), 10)

    def close(self, i, **kw):
        p = self.tmp / f"postings/industrial-design/p{i}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d.update(kw)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")

    def test_stop_when_too_many_unexpected_removals(self):
        for i in range(3):
            self.close(i, status="closed")
        before_csv = (self.tmp / "postings.csv").read_text(encoding="utf-8")
        b, code = self.build()
        self.assertEqual(code, 1)
        self.assertTrue(b.stopped)
        self.assertEqual((self.tmp / "postings.csv").read_text(encoding="utf-8"), before_csv)
        # Nothing was moved to the archive.
        self.assertTrue((self.tmp / "postings/industrial-design/p0.json").exists())
        self.assertIsNotNone(b.log_path)
        self.assertIn("Safety stop", (self.tmp / b.log_path).read_text(encoding="utf-8"))
        result = json.loads((self.tmp / "run/last-build.json").read_text(encoding="utf-8"))
        self.assertTrue(result["stopped"])

    def test_force_overrides(self):
        for i in range(3):
            self.close(i, status="closed")
        b, code = self.build(force=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.feed()), 7)
        self.assertTrue((self.tmp / "postings/_archive/industrial-design/p0.json").exists())

    def test_expired_deadlines_do_not_count(self):
        for i in range(5):
            self.close(i, deadline="2026-11-01")
        b, code = self.build()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.feed()), 5)

    def test_under_limit_is_fine(self):
        self.close(0, status="closed")
        self.close(1, status="closed")
        b, code = self.build()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.feed()), 8)


class LogTests(BoardTest):
    def test_log_name_and_sections(self):
        self.posting()
        b, _ = self.build(kind="daily")
        self.assertEqual(b.log_path.as_posix(), "logs/2026/11/2026-11-05_1005-CT_daily.md")
        text = (self.tmp / b.log_path).read_text(encoding="utf-8")
        heads = [l for l in text.splitlines() if l.startswith("## ")]
        self.assertEqual(heads, ["## 1. Summary", "## 2. New jobs", "## 3. Changes to existing jobs",
                                 "## 4. Errors and warnings", "## 5. Watchlist", "## 6. Skipped candidates",
                                 "## 7. All current jobs"])

    def test_edit_build_without_changes_writes_no_log(self):
        self.git("init", "-q")
        self.posting()
        self.build()
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        b, _ = self.build(kind="edit")
        self.assertIsNone(b.log_path)

    def test_collection_reads_pending(self):
        self.posting()
        self.put("run/pending.json", {
            "watchlist": [{"id": "sample-co", "status": "ok", "added": 1},
                          {"id": "acme", "status": "failed", "error": "HTTP 404",
                           "likelyCause": "the board moved"}],
            "skipped": [{"company": "Acme", "title": "Senior Designer", "url": "https://acme.com/1",
                         "reason": "senior role"}],
            "searches": [{"query": "industrial design intern 2027", "mode": "standard"}],
        })
        b, _ = self.build(kind="collection")
        text = (self.tmp / b.log_path).read_text(encoding="utf-8")
        self.assertIn("HTTP 404", text)
        self.assertIn("Guess: the board moved", text)
        self.assertIn("senior role", text)
        self.assertIn("industrial design intern 2027", text)
        self.assertIn("| Watchlist failures | 1 |", text)


if __name__ == "__main__":
    unittest.main()


class NoiseAndScopeTests(BoardTest):
    def test_blank_pay_term_only_listed_for_new_postings(self):
        self.git("init", "-q")
        self.posting(id="old-blank", pay="", term="", url="https://e.com/jobs/1", title="A")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        self.posting(id="new-blank", pay="", url="https://e.com/jobs/2", title="B")
        b, _ = self.build()
        warned = [p for p in b.problems() if p[0] == "Warning" and p[2].startswith("blank")]
        self.assertEqual([p[1].split("/")[-1] for p in warned], ["new-blank.json"])
        self.assertEqual(b.blank_count, 2)
        self.assertIn("Open postings with blank pay or term | 2", (self.tmp / b.log_path).read_text(encoding="utf-8"))

    def test_scope_flags(self):
        board = build_feed.Build(self.tmp, "edit", today=TODAY, now=NOW)
        flags = board.scope_flags
        self.assertTrue(flags({"title": "Industrial Design Intern",
                               "url": "https://co.com/jobs/design-engineering-internship-2027/"}))
        self.assertFalse(flags({"title": "Design Engineering Intern",
                                "url": "https://co.com/jobs/design-engineering-internship/"}))
        self.assertTrue(flags({"title": "Senior Graphic Designer", "url": "https://co.com/1"}))
        self.assertTrue(flags({"title": "Design Lead", "url": "https://co.com/1"}))
        self.assertFalse(flags({"title": "Junior Art Director", "url": "https://co.com/1"}))
        self.assertTrue(flags({"title": "Creative Director", "url": "https://co.com/1"}))
        self.assertFalse(flags({"title": "UX Designer Intern",
                                "url": "https://www.wayup.com/i-Engineering-j-UX-Designer-Intern-HDR-1/"}))
        self.assertTrue(flags({"title": "Design Intern", "url": "https://co.com/1", "pay": "Unpaid"}))

    def test_scope_flag_warns_on_new_posting(self):
        self.posting(id="maybe-eng", url="https://co.com/jobs/design-engineering-intern/")
        b, _ = self.build()
        self.assertTrue(any("possible off-scope" in p[2] for p in b.problems()))


class RecheckAndClosingSoonTests(BoardTest):
    def test_last_verified_only_is_a_recheck_not_a_change(self):
        self.git("init", "-q")
        self.posting(id="r1")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        p = self.tmp / "postings/industrial-design/r1.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d["lastVerified"] = "2026-11-05"
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        b, _ = self.build()
        self.assertEqual(b.changes, [])
        self.assertEqual([r.id for r in b.reverified], ["r1"])
        self.assertIsNotNone(b.log_path)

    def test_closing_soon_section(self):
        self.posting(id="soon", deadline="2026-11-12", url="https://e.com/jobs/1", title="A")
        self.posting(id="later", deadline="2026-12-30", url="https://e.com/jobs/2", title="B")
        b, _ = self.build(kind="daily")
        text = (self.tmp / b.log_path).read_text(encoding="utf-8")
        section = text.split("### Closing in the next 14 days")[1].split("### Every posting")[0]
        self.assertIn("2026-11-12", section)
        self.assertNotIn("2026-12-30", section)


class RoutineCheckTests(BoardTest):
    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        self.put("watchlist/industrial-design/feedco.json",
                 sample_watch(id="feedco", company="FeedCo", method="greenhouse", lastChecked="2026-11-01"))
        for i, day in enumerate(["2026-10-01", "2026-10-02"]):
            self.put(f"watchlist/industrial-design/s{i}.json",
                     sample_watch(id=f"s{i}", company=f"Search{i}", lastChecked=day))
        for i in range(3):
            self.posting(id=f"v{i}", url=f"https://e.com/jobs/{i}", title=f"T{i}", lastVerified="2026-10-20")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

    def set_watch(self, wid, **kw):
        p = self.tmp / f"watchlist/industrial-design/{wid}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d.update(kw)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")

    def set_post(self, pid, **kw):
        p = self.tmp / f"postings/industrial-design/{pid}.json"
        d = json.loads(p.read_text(encoding="utf-8"))
        d.update(kw)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")

    def good_pending(self):
        return {"weekday": "Thursday",
                "watchlist": [{"id": "feedco", "status": "ok", "added": 1},
                              {"id": "s0", "status": "ok", "added": 0},
                              {"id": "s1", "status": "ok", "added": 0}],
                "searches": [{"query": f"q{i}", "mode": "standard"} for i in range(15)],
                "verified": [{"id": f"v{i}", "result": "open"} for i in range(3)]}

    def run_checks(self, pending):
        self.put("run/pending.json", pending)
        b, _ = self.build(kind="collection")
        return {name: (ok, detail) for name, ok, detail in b.checks}, b

    def test_all_pass(self):
        for w in ("feedco", "s0", "s1"):
            self.set_watch(w, lastChecked="2026-11-05")
        for i in range(3):
            self.set_post(f"v{i}", lastVerified="2026-11-05")
        self.posting(id="new-one", url="https://e.com/jobs/new", title="New", source="Watchlist · FeedCo")
        checks, b = self.run_checks(self.good_pending())
        failed = [k for k, (ok, _) in checks.items() if not ok]
        self.assertEqual(failed, [])
        self.assertIn("| Routine check problems | 0 |", (self.tmp / b.log_path).read_text(encoding="utf-8"))

    def test_failures_are_caught(self):
        self.set_watch("feedco", lastChecked="2026-11-05")
        self.set_watch("s1", lastChecked="2026-11-05")      # changed but not reported
        self.posting(id="bad-src", url="https://e.com/jobs/x", title="X",
                     source="Search: Physical Design Internships board")
        pending = self.good_pending()
        pending["watchlist"] = [{"id": "feedco", "status": "ok"}]   # search entries missing
        pending["weekday"] = "Monday"
        pending["searches"] = pending["searches"][:2]
        pending["verified"] = [{"id": "v0", "result": "open"}]        # lastVerified not updated
        checks, b = self.run_checks(pending)
        failed = {k for k, (ok, _) in checks.items() if not ok}
        self.assertEqual(len(failed), 7, failed)
        log = (self.tmp / b.log_path).read_text(encoding="utf-8")
        self.assertIn("missing: s0, s1", log)
        self.assertIn("**FAIL**", log)
        self.assertEqual(b.result["routineCheckProblems"], 7)


class BlockedHostTests(BoardTest):
    def test_open_linkedin_posting_is_an_error(self):
        self.posting(id="li-open", url="https://www.linkedin.com/jobs/view/123/")
        b, _ = self.build()
        d = self.read("postings/_errors/li-open.json")
        self.assertIn("linkedin.com", d["errorDetail"])
        self.assertEqual(self.feed(), [])

    def test_closed_linkedin_posting_is_fine(self):
        self.posting(id="li-closed", url="https://www.linkedin.com/jobs/view/123/", status="closed",
                     closedAt="2026-11-06", closedReason="removed")
        self.build()
        self.assertTrue((self.tmp / "postings/_archive/industrial-design/li-closed.json").exists())
