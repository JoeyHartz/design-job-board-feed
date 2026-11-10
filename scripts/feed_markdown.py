"""Render the public Markdown page (postings.md) from the published feed rows.

Standard library only. Called by build_feed.py every time it writes postings.csv
and postings.json, so all three always show the same jobs. Uses only the public
feed fields (no source, status or internal notes).

Layout: title and summary, a table of contents, jobs closing soon, jobs added
this week, then one section per program, split by region, with "back to top"
links. Headings use GitHub's anchor rules so the contents links work on github.com.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import OrderedDict

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(text):
    """Escape characters that Markdown would treat as formatting."""
    text = " ".join(str(text or "").split())
    return re.sub(r"([\\`*_\[\]<>#|])", r"\\\1", text)


def nice_date(value, with_year=False):
    try:
        d = dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return esc(value)
    return f"{MONTHS[d.month - 1]} {d.day}" + (f", {d.year}" if with_year else "")


class Slugger:
    """GitHub's heading anchors: lowercase, drop punctuation, spaces to hyphens, -1/-2 for repeats."""

    def __init__(self):
        self.seen = {}

    def __call__(self, heading):
        slug = re.sub(r"[^\w\- ]", "", heading.lower().strip()).replace(" ", "-")
        n = self.seen.get(slug, 0)
        self.seen[slug] = n + 1
        return slug if n == 0 else f"{slug}-{n}"


def job_block(r, show_program=False):
    title = f"**[{esc(r.get('title'))}]({r.get('url', '')})**"
    line1 = " · ".join(x for x in (esc(r.get("company")), esc(r.get("program")) if show_program else "",
                                    esc(r.get("level")), esc(r.get("term"))) if x)
    bits = [esc(r.get("location"))]
    bits.append(f"Pay: {esc(r['pay'])}" if r.get("pay") else "Pay: not listed")
    if r.get("deadline"):
        bits.append(f"**Apply by {nice_date(r['deadline'], True)}**")
    bits.append(f"Found {nice_date(r.get('firstSeen'))}")
    lines = [title + "  ", line1 + "  ", " · ".join(b for b in bits if b) + ("  " if r.get("note") else "")]
    if r.get("note"):
        lines.append(f"_{esc(r['note'])}_")
    return "\n".join(lines)


def render(rows, cfg, today: dt.date, csv_name="postings.csv", json_name="postings.json"):
    """rows: feed rows (dicts with the posting fields plus 'program'), already sorted newest first."""
    page = cfg.get("feed", {}).get("markdown", {})
    title = page.get("title") or cfg.get("name", "Job board")
    slug = Slugger()
    top = slug(title)
    regions = cfg.get("regions", [])
    labels = [cfg["programs"][p]["label"] for p in cfg.get("programOrder", cfg["programs"])]
    labels += [c["label"] for c in cfg.get("comboLabels", [])]
    soon_days = cfg.get("thresholds", {}).get("closingSoonDays", 14)
    new_days = page.get("newDays", 7)

    def as_date(v):
        try:
            return dt.date.fromisoformat(v)
        except (TypeError, ValueError):
            return None

    soon = sorted((r for r in rows if as_date(r.get("deadline")) and as_date(r["deadline"]) <= today + dt.timedelta(days=soon_days)),
                  key=lambda r: (r["deadline"], r.get("company", "").casefold()))
    new = [r for r in rows if as_date(r.get("firstSeen")) and as_date(r["firstSeen"]) > today - dt.timedelta(days=new_days)]

    groups = OrderedDict((lab, OrderedDict((reg, []) for reg in regions)) for lab in labels)
    for r in rows:
        groups.setdefault(r.get("program", "Other"), OrderedDict((reg, []) for reg in regions))
        groups[r.get("program", "Other")].setdefault(r.get("region", "Other"), []).append(r)

    # Work out every heading's anchor in page order, so contents links match.
    a_soon, a_new = slug("Closing soon"), slug("New this week")
    anchors = {}
    for lab, by_reg in groups.items():
        if not any(by_reg.values()):
            continue
        anchors[lab] = slug(lab)
        for reg, items in by_reg.items():
            if items:
                anchors[(lab, reg)] = slug(f"{lab} · {reg}")
    a_about = slug("About this list")

    out = [f"# {esc(title)}", ""]
    if page.get("intro") or cfg.get("description"):
        out += [esc(page.get("intro") or cfg.get("description")), ""]
    out += [f"**{len(rows)} open job{'s' if len(rows) != 1 else ''}** · updated {nice_date(today.isoformat(), True)} · "
            f"download as [CSV]({csv_name}) or [JSON]({json_name})", ""]
    if page.get("notice"):
        out += [f"> {esc(page['notice'])}", ""]

    out += ["## Contents", ""]
    out.append(f"- [Closing soon](#{a_soon}) ({len(soon)})")
    out.append(f"- [New this week](#{a_new}) ({len(new)})")
    for lab, by_reg in groups.items():
        if lab not in anchors:
            continue
        n = sum(len(v) for v in by_reg.values())
        regs = " · ".join(f"[{esc(reg)}](#{anchors[(lab, reg)]}) ({len(items)})" for reg, items in by_reg.items() if items)
        out.append(f"- **[{esc(lab)}](#{anchors[lab]})** ({n}): {regs}")
    out += [f"- [About this list](#{a_about})", ""]

    back = f"[Back to top](#{top})"
    out += ["## Closing soon", "", f"Jobs with an apply-by date in the next {soon_days} days, soonest first.", ""]
    if soon:
        out += ["| Apply by | Job | Company | Program |", "|---|---|---|---|"]
        for r in soon:
            out.append(f"| {nice_date(r['deadline'])} | [{esc(r.get('title'))}]({r.get('url', '')}) | "
                       f"{esc(r.get('company'))} | {esc(r.get('program'))} |")
    else:
        out.append("None right now.")
    out += ["", back, ""]

    out += ["## New this week", "", f"Added in the last {new_days} days, newest first.", ""]
    if new:
        for r in new:
            out += [job_block(r, show_program=True), ""]
    else:
        out += ["None this week.", ""]
    out += [back, ""]

    for lab, by_reg in groups.items():
        if lab not in anchors:
            continue
        n = sum(len(v) for v in by_reg.values())
        out += [f"## {esc(lab)}", "", f"{n} job{'s' if n != 1 else ''}: " + " · ".join(
            f"[{esc(reg)}](#{anchors[(lab, reg)]}) ({len(items)})" for reg, items in by_reg.items() if items), ""]
        for reg, items in by_reg.items():
            if not items:
                continue
            out += [f"### {esc(lab)} · {esc(reg)}", ""]
            for r in items:
                out += [job_block(r), ""]
            out += [back, ""]

    out += ["## About this list", ""]
    about = page.get("about") or []
    for para in about:
        out += [esc(para), ""]
    out += [f"Regions: {', '.join(esc(r) for r in regions)}. \"Midwest\" means Illinois outside Chicagoland and nearby "
            "states; \"International\" includes Canada.", "", back, ""]
    return "\n".join(out)
