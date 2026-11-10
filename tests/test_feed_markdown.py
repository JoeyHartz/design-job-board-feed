"""Tests for scripts/feed_markdown.py (the public postings.md page)."""

import datetime as dt
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import feed_markdown  # noqa: E402

CFG = json.loads((REPO / "config" / "board.json").read_text(encoding="utf-8"))
TODAY = dt.date(2026, 11, 10)


def row(**kw):
    r = {"id": "x", "program": "Industrial Design", "title": "Design Intern", "company": "Acme",
         "level": "Internship", "term": "Summer 2027", "location": "Chicago, IL", "region": "Chicago area",
         "pay": "$25/hr", "deadline": "", "url": "https://acme.com/jobs/1", "firstSeen": "2026-11-01",
         "note": "Consumer products.", "source": "SECRET SOURCE", "status": "open"}
    r.update(kw)
    return r


class SlugTests(unittest.TestCase):
    def test_github_rules(self):
        s = feed_markdown.Slugger()
        self.assertEqual(s("UI/UX & Digital Product"), "uiux--digital-product")
        self.assertEqual(s("ID + GD"), "id--gd")
        self.assertEqual(s("Industrial Design · Chicago area"), "industrial-design--chicago-area")
        self.assertEqual(s("ID + GD"), "id--gd-1")


class PageTests(unittest.TestCase):
    def render(self, rows):
        return feed_markdown.render(rows, CFG, TODAY)

    def test_every_contents_link_has_a_heading(self):
        rows = [row(id="a"), row(id="b", program="UI/UX & Digital Product", region="Remote", url="https://x/2"),
                row(id="c", program="ID + GD", region="International", url="https://x/3")]
        text = self.render(rows)
        s = feed_markdown.Slugger()
        anchors = {s(h) for h in re.findall(r"^#{1,3} (.+)$", text, re.M)}
        links = set(re.findall(r"\]\(#([^)]+)\)", text))
        self.assertTrue(links)
        self.assertEqual(links - anchors, set())

    def test_sections_and_counts(self):
        rows = [row(id="soon", deadline="2026-11-15", firstSeen="2026-11-09", url="https://x/1"),
                row(id="old", firstSeen="2026-10-01", url="https://x/2")]
        text = self.render(rows)
        self.assertIn("**2 open jobs**", text)
        closing = text.split("## Closing soon")[1].split("## New this week")[0]
        self.assertIn("Nov 15", closing)
        new = text.split("## New this week")[1].split("## Industrial Design")[0]
        self.assertIn("https://x/1", new)
        self.assertNotIn("https://x/2", new)

    def test_private_fields_never_shown(self):
        text = self.render([row()])
        self.assertNotIn("SECRET SOURCE", text)

    def test_markdown_in_titles_is_escaped(self):
        text = self.render([row(title="Designer | *Junior* [2027]")])
        self.assertIn("Designer \\| \\*Junior\\* \\[2027\\]", text)

    def test_empty_feed(self):
        text = self.render([])
        self.assertIn("**0 open jobs**", text)
        self.assertIn("None right now.", text)


if __name__ == "__main__":
    unittest.main()
