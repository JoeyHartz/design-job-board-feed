"""Offline tests for the maintenance scripts: close_expired, check_links,
collect_candidates and weekly_digest. No network."""

import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLES = Path(__file__).resolve().parent / "samples"
sys.path.insert(0, str(REPO / "scripts"))

import check_links  # noqa: E402
import close_expired  # noqa: E402
import collect_candidates  # noqa: E402
import weekly_digest  # noqa: E402

TODAY = dt.date(2026, 11, 6)
NOW = dt.datetime(2026, 11, 6, 5, 40)


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="maint-test-"))
        shutil.copytree(REPO / "config", self.tmp / "config")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def posting(self, folder="industrial-design", **kw):
        d = json.loads((SAMPLES / "posting.json").read_text(encoding="utf-8"))
        d.update(kw)
        p = self.tmp / "postings" / folder / f"{d['id']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return p

    def watch(self, **kw):
        d = json.loads((SAMPLES / "watchlist.json").read_text(encoding="utf-8"))
        d.update(kw)
        p = self.tmp / "watchlist" / "industrial-design" / f"{d['id']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")


class CloseExpiredTests(Repo):
    def test_only_open_past_deadlines_close(self):
        past = self.posting(id="past", deadline="2026-11-05")
        due_today = self.posting(id="today", deadline="2026-11-06")
        no_dl = self.posting(id="nodl", deadline="")
        hidden = self.posting(id="hidden", deadline="2026-10-01", status="hidden")
        closed = close_expired.close_expired(self.tmp, TODAY)
        self.assertEqual([c[0] for c in closed], ["past"])
        d = json.loads(past.read_text(encoding="utf-8"))
        self.assertEqual((d["status"], d["closedAt"], d["lastUpdated"]), ("closed", "2026-11-06", "2026-11-06"))
        self.assertIn("deadline passed", d["closedReason"])
        self.assertEqual(d["statusHistory"][-1]["status"], "closed")
        for p in (due_today, no_dl):
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["status"], "open")
        self.assertEqual(json.loads(hidden.read_text(encoding="utf-8"))["status"], "hidden")

    def test_pending_past_deadline_closes(self):
        p = self.posting(id="pend", deadline="2026-11-01", status="pending")
        self.assertEqual([c[0] for c in close_expired.close_expired(self.tmp, TODAY)], ["pend"])
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["status"], "closed")

    def test_dry_run_writes_nothing(self):
        p = self.posting(id="past", deadline="2026-11-01")
        before = p.read_text(encoding="utf-8")
        self.assertEqual(len(close_expired.close_expired(self.tmp, TODAY, dry_run=True)), 1)
        self.assertEqual(p.read_text(encoding="utf-8"), before)


def fake(responses):
    """fetcher returning canned (status, final_url, body) by URL; Exceptions are raised."""
    def fetcher(url):
        r = responses[url]
        if isinstance(r, Exception):
            raise r
        return r
    return fetcher


class LinkCheckTests(unittest.TestCase):
    def test_classify(self):
        u = "https://example.com/jobs/12345-design-intern"
        cases = {
            "gone-404": ({u: (404, u, "Job not found")}, "gone"),
            "gone-redirect": ({u: (200, "https://example.com/careers", "")}, "gone"),
            "ok": ({u: (200, u, "<h1>Design Intern</h1> Apply")}, "ok"),
            "blocked": ({u: (403, u, "Attention Required! | Cloudflare")}, "unreachable"),
            "blocked-404": ({u: (404, u, "Sorry, you have been blocked")}, "unreachable"),
            "server-error": ({u: (503, u, "")}, "unreachable"),
            "network": ({u: urllib.error.URLError("timed out")}, "unreachable"),
        }
        for name, (responses, want) in cases.items():
            self.assertEqual(check_links.classify(u, fake(responses))[0], want, name)

    def test_greenhouse_error_redirect(self):
        u = "https://job-boards.greenhouse.io/acme/jobs/4669079008"
        r = {u: (200, "https://job-boards.greenhouse.io/acme?error=true", "")}
        self.assertEqual(check_links.classify(u, fake(r))[0], "gone")

    def test_workday_uses_data_endpoint(self):
        u = "https://acme.wd5.myworkdayjobs.com/Careers/job/Warren-MI/Design-Intern_JR-1234567"
        api = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/job/Warren-MI/Design-Intern_JR-1234567"
        self.assertEqual(check_links.classify(u, fake({api: (404, api, "")}))[0], "gone")
        self.assertEqual(check_links.classify(u, fake({api: (200, api, "{}")}))[0], "ok")

    def test_same_page_is_not_general(self):
        self.assertFalse(check_links.looks_general("https://a.com/jobs/123", "https://a.com/jobs/123/"))
        self.assertTrue(check_links.looks_general("https://a.com/jobs/123", "https://a.com/jobs"))


class CollectTests(Repo):
    def test_first_listed_carries_over_and_failures_recorded(self):
        self.watch(id="good", company="Good Co", method="greenhouse",
                   sourceUrl="https://boards-api.greenhouse.io/v1/boards/good/jobs")
        self.watch(id="bad", company="Bad Co", method="workday", sourceUrl="https://bad.wd1.myworkdayjobs.com/x")
        self.watch(id="srch", company="Search Co", method="search")
        (self.tmp / "reports").mkdir()
        (self.tmp / "reports" / "candidates.json").write_text(json.dumps({
            "date": "2026-11-05", "entries": [{"id": "good", "jobs": [
                {"url": "https://x/old", "firstListed": "2026-11-03"}]}]}), encoding="utf-8")

        def fetcher(method, url, known):
            if method == "workday":
                raise urllib.error.URLError("boom")
            return ([("Designer", "Chicago", "", "https://x/old"), ("Design Intern", "Remote", "", "https://x/new")], 10, 1)

        rep = collect_candidates.collect(self.tmp, TODAY, NOW, fetcher=fetcher, workers=1, known=set())
        by_id = {e["id"]: e for e in rep["entries"]}
        self.assertEqual(set(by_id), {"good", "bad"})            # search entries aren't job lists
        self.assertEqual(by_id["bad"]["status"], "failed")
        self.assertIn("boom", by_id["bad"]["error"])
        first = {j["url"]: j["firstListed"] for j in by_id["good"]["jobs"]}
        self.assertEqual(first, {"https://x/old": "2026-11-03", "https://x/new": "2026-11-06"})
        text = collect_candidates.to_markdown(rep)
        self.assertIn("**1 first listed today**", text)


class DigestTests(Repo):
    def test_counts_and_sections(self):
        self.posting(id="new1", firstSeen="2026-11-04", title="A", url="https://e.com/1")
        self.posting(id="old1", firstSeen="2026-10-01", title="B", url="https://e.com/2")
        self.posting(id="gone1", status="closed", closedAt="2026-11-05", firstSeen="2026-10-01", closedReason="deadline passed (2026-11-04)",
                     folder="_archive/industrial-design", title="C", url="https://e.com/3")
        (self.tmp / "postings.json").write_text(json.dumps([
            {"id": "new1", "program": "Industrial Design", "company": "Sample Co", "title": "A",
             "apply_by": "2026-11-12", "link": "https://e.com/1"},
            {"id": "old1", "program": "Industrial Design", "company": "Sample Co", "title": "B",
             "apply_by": "", "link": "https://e.com/2"}]), encoding="utf-8")
        text = weekly_digest.digest(self.tmp, TODAY)
        self.assertIn("| Added this week | 1 |", text)
        self.assertIn("| Closed this week | 1 |", text)
        self.assertIn("1 deadline passed", text)
        self.assertIn("2026-11-12", text)
        self.assertIn("Link check: no report yet", text)

    def test_pending_waiting_long_is_flagged(self):
        self.posting(id="wait", status="pending", firstSeen="2026-10-25", title="W", url="https://e.com/9")
        (self.tmp / "postings.json").write_text("[]", encoding="utf-8")
        text = weekly_digest.digest(self.tmp, TODAY)
        self.assertIn("| Waiting for your approval | 1 |", text)
        self.assertIn("for more than 7 days", text)


if __name__ == "__main__":
    unittest.main()
