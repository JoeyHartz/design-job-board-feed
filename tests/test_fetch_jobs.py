"""Offline tests for scripts/fetch_jobs.py (no network)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fetch_jobs  # noqa: E402


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.words = fetch_jobs.keywords()

    def test_design_titles_kept_engineering_dropped(self):
        keep = ["2027 Summer Intern - Industrial Design", "Junior Graphic Designer", "CMF Designer",
                "UX Design Intern", "Packaging Designer", "2027 Summer Intern - Clay Sculpting",
                "Production Artist", "Junior Art Director"]
        drop = ["Design Release Engineer - Bumper", "Senior Design Engineer", "Accountant",
                "Promotion Coordinator", "Manufacturing Engineering Co-op"]
        for t in keep:
            self.assertTrue(fetch_jobs.matches(t, self.words), t)
        for t in drop:
            self.assertFalse(fetch_jobs.matches(t, self.words), t)


class KnownJobTests(unittest.TestCase):
    def test_requisition_matches_across_sites(self):
        a = fetch_jobs.job_keys("https://search-careers.gm.com/en/jobs/jr-202621626/summer-2027-intern-industrial-design/")
        b = fetch_jobs.job_keys("https://generalmotors.wd5.myworkdayjobs.com/Careers_GM/job/Warren/Summer-2027-Intern_JR-202621626")
        self.assertTrue(a & b)

    def test_short_requisition_only_matches_same_host(self):
        a = fetch_jobs.job_keys("https://andersen.wd12.myworkdayjobs.com/x/job/y/Design-Intern_R-8220")
        b = fetch_jobs.job_keys("https://other.wd1.myworkdayjobs.com/x/job/y/Design-Intern_R-8220")
        self.assertFalse(a & b)

    def test_same_url_with_slash_and_case(self):
        a = fetch_jobs.job_keys("https://www.Example.com/jobs/Designer/")
        b = fetch_jobs.job_keys("https://example.com/jobs/designer")
        self.assertTrue(a & b)


class WorkdayTests(unittest.TestCase):
    def test_paging_and_urls(self):
        pages = [
            {"total": 3, "jobPostings": [
                {"title": "Industrial Design Intern", "locationsText": "Warren, MI", "postedOn": "Posted Today",
                 "externalPath": "/job/Warren/Industrial-Design-Intern_JR-1"},
                {"title": "Accountant", "locationsText": "Detroit, MI", "postedOn": "", "externalPath": "/job/x_JR-2"}]},
            {"total": 3, "jobPostings": [
                {"title": "Graphic Designer", "locationsText": "Remote", "postedOn": "",
                 "externalPath": "/job/r/Graphic-Designer_JR-3"}]},
        ]
        calls = []

        def fake_http(url, data=None):
            calls.append((url, data))
            return pages[min(data["offset"] // 2, 1)] if data["offset"] < 3 else {"total": 3, "jobPostings": []}

        with mock.patch.object(fetch_jobs, "http", side_effect=fake_http):
            rows = list(fetch_jobs.workday("https://acme.wd5.myworkdayjobs.com/en-US/Acme_Careers"))
        self.assertEqual(calls[0][0], "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Acme_Careers/jobs")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][3], "https://acme.wd5.myworkdayjobs.com/Acme_Careers/job/Warren/Industrial-Design-Intern_JR-1")

    def test_missing_site_is_an_error(self):
        with self.assertRaises(ValueError):
            list(fetch_jobs.workday("https://acme.wd5.myworkdayjobs.com/"))


class OtherSystemsTests(unittest.TestCase):
    def test_greenhouse(self):
        data = {"jobs": [{"title": "Brand Designer", "location": {"name": "Chicago, IL"},
                          "updated_at": "2026-11-01T00:00:00Z", "absolute_url": "https://x/1"}]}
        with mock.patch.object(fetch_jobs, "http", return_value=data):
            shown, total = fetch_jobs.run("greenhouse", "https://boards-api.greenhouse.io/v1/boards/x/jobs")
        self.assertEqual((len(shown), total), (1, 1))
        self.assertEqual(shown[0], ("Brand Designer", "Chicago, IL", "2026-11-01", "https://x/1"))

    def test_lever_adds_mode_json(self):
        seen = []
        with mock.patch.object(fetch_jobs, "http", side_effect=lambda u, d=None: seen.append(u) or []):
            fetch_jobs.run("lever", "https://api.lever.co/v0/postings/acme")
        self.assertTrue(seen[0].endswith("?mode=json"))

    def test_unknown_method(self):
        with self.assertRaises(ValueError):
            fetch_jobs.run("search", "")


if __name__ == "__main__":
    unittest.main()


class RetryTests(unittest.TestCase):
    def test_retries_temporary_errors(self):
        import io
        import urllib.error
        calls = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)
            return Resp(b'{"ok": true}')

        with mock.patch.object(fetch_jobs.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.object(fetch_jobs.time, "sleep", lambda s: None):
            self.assertEqual(fetch_jobs.http("https://x"), {"ok": True})
        self.assertEqual(len(calls), 3)

    def test_does_not_retry_404(self):
        import urllib.error
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        with mock.patch.object(fetch_jobs.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.object(fetch_jobs.time, "sleep", lambda s: None):
            with self.assertRaises(urllib.error.HTTPError):
                fetch_jobs.http("https://x")
        self.assertEqual(len(calls), 1)
