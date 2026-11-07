"""Offline tests for scripts/approvals.py."""

import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAMPLES = Path(__file__).resolve().parent / "samples"
sys.path.insert(0, str(REPO / "scripts"))

import approvals  # noqa: E402

TODAY = dt.date(2026, 11, 9)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="approve-test-"))
        shutil.copytree(REPO / "config", self.tmp / "config")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def posting(self, **kw):
        d = json.loads((SAMPLES / "posting.json").read_text(encoding="utf-8"))
        d.update(kw)
        p = self.tmp / "postings" / "industrial-design" / f"{d['id']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        return p

    def status(self, p):
        return json.loads(p.read_text(encoding="utf-8"))["status"]

    def body(self):
        return approvals.render(self.tmp, TODAY)[0]

    def tick(self, body, action, pid):
        return body.replace(f"- [ ] {action} `{pid}`", f"- [x] {action} `{pid}`")

    def test_render_lists_only_pending(self):
        self.posting(id="p1", status="pending", title="Design Intern")
        self.posting(id="o1", status="open", title="Other")
        body, n = approvals.render(self.tmp, TODAY)
        self.assertEqual(n, 1)
        self.assertIn("- [ ] Approve `p1`", body)
        self.assertIn("- [ ] Reject `p1`", body)
        self.assertNotIn("o1", body)
        self.assertIn("Approve all", body)

    def test_render_flags_long_waits(self):
        self.posting(id="old", status="pending", firstSeen="2026-10-20")
        self.assertIn("waiting 20 days", self.body())

    def test_approve_and_reject(self):
        a = self.posting(id="a", status="pending", title="A", url="https://e.com/1")
        r = self.posting(id="r", status="pending", title="R", url="https://e.com/2")
        u = self.posting(id="u", status="pending", title="U", url="https://e.com/3")
        body = self.tick(self.tick(self.body(), "Approve", "a"), "Reject", "r")
        done = approvals.apply(self.tmp, body, TODAY, "JoeyHartz")
        self.assertEqual((done["approved"], done["rejected"]), (["a"], ["r"]))
        self.assertEqual((self.status(a), self.status(r), self.status(u)), ("open", "hidden", "pending"))
        hist = json.loads(a.read_text(encoding="utf-8"))["statusHistory"][-1]
        self.assertEqual(hist["reason"], "approved by JoeyHartz")

    def test_approve_all_respects_reject(self):
        a = self.posting(id="a", status="pending", title="A", url="https://e.com/1")
        r = self.posting(id="r", status="pending", title="R", url="https://e.com/2")
        body = self.tick(self.body(), "Reject", "r").replace("- [ ] **Approve all**", "- [x] **Approve all**")
        approvals.apply(self.tmp, body, TODAY)
        self.assertEqual((self.status(a), self.status(r)), ("open", "hidden"))

    def test_both_ticked_is_left_pending(self):
        p = self.posting(id="both", status="pending")
        body = self.tick(self.tick(self.body(), "Approve", "both"), "Reject", "both")
        done = approvals.apply(self.tmp, body, TODAY)
        self.assertEqual(done["conflicts"], ["both"])
        self.assertEqual(self.status(p), "pending")

    def test_only_pending_files_change(self):
        o = self.posting(id="o", status="open")
        body = "- [x] Reject `o`\n"
        done = approvals.apply(self.tmp, body, TODAY)
        self.assertEqual(done["notPending"], ["o"])
        self.assertEqual(self.status(o), "open")


if __name__ == "__main__":
    unittest.main()
