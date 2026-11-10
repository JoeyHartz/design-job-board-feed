# ID + GD Job Board

Open internships, co-ops and entry-level roles for Industrial Design, Graphic Design and UI/UX students in the School of Art & Design at the University of Illinois Urbana-Champaign. Updated each weekday.

**The published feed:**

- **[Browse the open jobs →](postings.md)** `postings.md` is the readable page: jobs closing soon, new this week, then every job by program and region, with a table of contents.
- `postings.csv` and `postings.json` list the same open postings as data. Columns: Program, Title, Company, Type, Term, Location, Region, Pay, Apply by, Found, Note, Link.
- Listings are gathered automatically and can close without notice. Confirm details on the employer's site before applying.

This repo is the only source of data. Every posting and every watchlist entry is one small JSON file. A GitHub Action rebuilds the feed whenever a file changes, and once a day.

## What the folders mean

| Folder | What's in it |
|---|---|
| `postings/industrial-design/`, `graphic-design/`, `ui-ux/` | Live postings for one program |
| `postings/multi-program/` | Live postings open to two or more programs |
| `postings/_archive/<program>/` | Closed and hidden postings, kept for the record |
| `postings/_errors/` | Postings the build couldn't use (see "Fix an error") |
| `watchlist/<program>/`, `watchlist/multi-program/` | Companies and job boards the daily routine checks |
| `watchlist/_inactive/<program>/` | Watchlist entries that are switched off |
| `watchlist/_errors/` | Watchlist entries the build couldn't use |
| `reports/` | Written by the daily maintenance Action: `candidates.md` (new jobs from company job lists), `links.md` (open postings whose links look gone), `digest.md` (the weekly summary) |
| `logs/YYYY/MM/` | One Markdown log per build that changed something, and one per daily run |
| `config/board.json` | Programs, allowed values, feed columns, time zone and thresholds |
| `config/criteria.md` | What counts as a qualifying job |
| `ROUTINE.md` | Steps for the daily collection run |
| `schema/` | The file formats, as JSON Schema |
| `scripts/` | `build_feed.py` (the build) and `list_index.py` (a one-line-per-file index) |
| `tests/` | Tests for the build, with sample files |

**You don't need to put a file in the right folder.** The build reads the fields and moves the file where it belongs. A file is named `<id>.json`, and the build fixes the name if it doesn't match `id`.

## Approving new jobs

Every job the routine finds starts as **`"pending"`**: it's checked and written up, but not published. You decide what goes on the board.

1. After each routine run, the **"Jobs waiting for approval"** GitHub issue lists every pending job with its program, level, term, location, pay, deadline, note and link. GitHub notifies you (the issue mentions you) when new jobs are added.
2. Tick **Approve** to publish a job, or **Reject** to keep it off the board for good. You can do this on github.com or in the GitHub phone app. To approve everything listed, tick **Approve all** at the top and tick **Reject** on any you don't want.
3. About a minute later the **Approvals** Action applies your ticks (approved → `"open"`, rejected → `"hidden"`, each with a dated history line naming you), starts **Build feed** to publish, and refreshes the list. Jobs you leave unticked stay in the list. When nothing is waiting, the issue closes itself.

Only the people listed in `approval.approvers` in `config/board.json` (and repo admins) can approve; ticks by anyone else are ignored. Pending jobs whose deadline passes are closed by the daily maintenance, and jobs waiting more than 7 days are flagged in the weekly digest. You can also approve by hand: change `"pending"` to `"open"` in the file. To turn approval off, set `approval.required` to `false`.

The jobs that were already published when approval was switched on (Nov 7, 2026) stayed published.

## Editing by hand

Edit files right on GitHub: open the file, click the pencil, change the value, and commit to `main`. The Action runs within a minute or two and updates the feed. Keep the JSON valid: quotes around text, commas between fields, no comma after the last field.

### Hide or close a job

Open the posting and change `"status"`:

- `"closed"` when the job is filled or gone. Also fill in `"closedAt"` (today, `YYYY-MM-DD`) and `"closedReason"` (a few words).
- `"hidden"` to keep it off the board for any other reason. The daily routine never touches hidden postings.

Then add a line to `statusHistory`, for example `{"date": "2026-11-06", "status": "closed", "reason": "filled"}`, and set `lastUpdated` to today. The build moves the file to `_archive/` and drops it from the feed. To bring a posting back, set `"status"` to `"open"`.

### Fix a job's details

Change the field, set `lastUpdated` to today, and commit. Allowed values:

- `programs`: any of `"ID"`, `"GD"`, `"UX"`, as a list, e.g. `["ID", "GD"]`.
- `level`: `"Internship"`, `"Co-op"` or `"Entry-level"`.
- `region`: `"Chicago area"`, `"Midwest"`, `"Remote"`, `"Elsewhere in US"` or `"International"`.
- `deadline`: `YYYY-MM-DD`, or `""` if there's none.
- `url`: must start with `http`.

### Add a job by hand

Copy `tests/samples/posting.json` into `postings/`, rename it to a new id (lowercase words with hyphens, e.g. `acme-graphic-design-intern-summer-2027.json`), and fill in every field. Set `id` to the same name without `.json`. Set `firstSeen`, `lastUpdated` and `lastVerified` to today. Write `facts` in your own words, never pasted posting text.

### Fix an error

When a file breaks a rule (a missing title, a misspelled region, a bad date or broken JSON), the build leaves it out of the feed and moves it to `_errors/`. If the JSON was readable, it also sets `"status": "error"`, saves the old status in `"previousStatus"`, and writes the reason in `"errorDetail"`. The Action opens a GitHub issue that links to the log.

To fix it, open the file in `_errors/`, correct the problem named in `errorDetail`, and commit. Leave `status`, `previousStatus` and `errorDetail` alone: on the next build, the file passes, gets its old status back, has the error fields cleared, and moves back into place. For a file that had broken JSON, fix the JSON and make sure `status` is right.

### Change a watchlist entry

Each entry has a `method` that tells the routine how to check it: `greenhouse`, `lever`, `ashby`, `bamboohr`, `smartrecruiters` or `workday` (a company job system with a public job list in `sourceUrl`, read by `scripts/fetch_jobs.py`; for Workday, `sourceUrl` is the careers site root such as `https://generalmotors.wd5.myworkdayjobs.com/Careers_GM`), `justidjobs` (the Just ID Jobs board, whose page carries its job data; read by `scripts/fetch_jobs.py`, keeping the levels named in the link), `page` (a careers page in `sourceUrl`), `board` (a job board with its own steps in `ROUTINE.md`), or `search` (a web search by company name).

- **Turn an entry off:** set `"status": "inactive"` and add a `statusHistory` line with the reason. The build moves it to `_inactive/`. Set `"active"` to turn it back on.
- **Change how it's checked:** edit `method`, `sourceUrl` or `careersUrl`.
- **Add a company:** copy `tests/samples/watchlist.json` into `watchlist/`, rename it, and fill it in.

The daily routine never adds, removes, renames or changes the status of watchlist entries. Only you do.

## How the build works

`scripts/build_feed.py` uses the Python standard library only. It runs in this order:

1. **Validate** every file. Errors go to `_errors/` (see above). Warnings are logged but still published: possible duplicates, stale postings, a filename that doesn't match `id` (fixed automatically), and, for new postings only, blank pay or term and **possible off-scope** hints. Blank pay or term on older postings is shown as one count, not a row each.
2. **Sort** files into the folder their fields call for, using `git mv` so history is kept.
3. **Find duplicates:** same id, same job URL, or same company + title + term. A shared general careers page only counts when the titles also match. Duplicates are merged into one feed entry with the programs combined; the most recently committed file wins, and fields that disagree are logged. No file is edited or deleted.
4. **Build the feed:** postings with status `open`, whose deadline is empty or today or later, and that aren't stale. Dates use Chicago time. Newest `firstSeen` first, then company. A posting is **stale** when its link is on linkedin.com, it has no deadline, and more than 60 days have passed since the later of `firstSeen` and `lastUpdated`. It stays open in its folder but leaves the feed.
5. **Safety stop:** if more than 20% of the previous feed would disappear for any reason other than a passed deadline or the stale rule, the build writes no feed files and moves nothing, writes the log, and fails. The Action opens an issue. After checking the log, a person can override it from the Actions tab: run **Build feed** by hand with **force** ticked.
6. **Write** `postings.csv`, `postings.json`, the public page `postings.md` (from `scripts/feed_markdown.py`; its title and text are in `feed.markdown` in `config/board.json`) and the log.

**Possible off-scope hints** flag a new posting for a quick look by hand, never removing it: the link mentions engineering but the title doesn't, the title says senior, lead, manager, director, principal, head of or staff ("art director" is allowed), or the pay or note says unpaid. The word lists are in `scopeFlags` in `config/board.json`.

**Routine checks.** On a daily collection run, the build compares what the routine reported in `run/pending.json` with the files and lists each check as pass or FAIL in section 4 of the log: every feed, page and board entry and the day's 12 search entries were reported; `lastChecked` changed only on entries reported as checked; 14–18 general searches ran; the weekday matches the Chicago date; new postings use the exact "Watchlist · <name>" source label; and at least 20 old postings were re-checked, with results that match the files.

**Re-checks.** Each run the routine re-opens about 25 open postings with the oldest `lastVerified` (LinkedIn links are skipped, since they can't be read), closes the ones that are gone and updates `lastVerified` on the rest. That covers the board about every two to three weeks. Section 3 of the log lists them. Postings closed this way count toward the safety stop, so a bad run can't empty the feed.

Program labels in the feed: both ID and GD → "ID + GD" (even if UX is also listed); one program → "Industrial Design", "Graphic Design" or "UI/UX & Digital Product"; ID + UX or GD + UX → the label of the first program listed.

### The Action

`.github/workflows/build-feed.yml` runs on every push that changes `postings/`, `watchlist/`, `config/` or `scripts/`, when started by hand, and daily at 16:05 UTC. It runs the tests, builds, and commits the results with `[skip ci]`. On a failed build, a safety stop or any file error, it opens an issue labeled `build-problem`, or comments on the one already open, with a link to the log.

### Daily maintenance (no Claude needed)

`.github/workflows/maintenance.yml` runs every day at 11:40 UTC (5:40 AM CST, 6:40 AM CDT), before the 7:35 routine:

1. **Closes postings whose deadline has passed** (`scripts/close_expired.py`), with `closedReason` "deadline passed (date)". Hidden postings are never touched.
2. **Collects candidate jobs** (`scripts/collect_candidates.py`): reads every watchlist entry that has a public job list and writes `reports/candidates.md` (and `.json`): design-titled jobs not yet on the board, with the date each was first listed. The routine reviews these instead of fetching the lists itself.
3. **Checks links** (`scripts/check_links.py`): opens every open posting's link (LinkedIn skipped) and writes `reports/links.md` (and `.json`) listing the ones that look gone. It never closes anything: the routine confirms each one first, because some sites answer scripts differently.
4. **Rebuilds the feed** and commits.

Every Monday it also opens a **weekly digest** issue (label `weekly-digest`; GitHub emails you when it opens, if you watch the repo): jobs added by program, jobs closed, jobs closing in the next 14 days, the latest link check and candidate counts, and any daily runs whose routine checks failed. Last week's digest issue is closed automatically.

You can run it by hand from the **Actions** tab: **Daily maintenance** → **Run workflow**, choosing `daily`, `digest` or `both`. If it fails, it opens or comments on the `build-problem` issue.

### Run it yourself

```bash
python3 -m unittest discover -s tests -v
```

```bash
python3 scripts/build_feed.py --dry-run
```

```bash
python3 scripts/list_index.py
```

`python3 scripts/fetch_jobs.py <watchlist id>` prints the design-titled jobs in that entry's job list (`--all` for every job).

`list_index.py` also takes `postings`, `watchlist`, `searches` (the 12 search entries due today) or `recheck` (the postings due for a re-check today).

## Reading a log

Logs are in `logs/YYYY/MM/`, named like `2026-11-06_0742-CT_collection.md`. The last word is the kind of run: `collection` (the daily routine), `daily` (the Action's daily rebuild), `edit` (a rebuild after someone changed a file) or `import` (the first import). Each log has seven sections:

1. **Summary:** counts of added, changed, closed, errors, warnings, stale, jobs in the feed and watchlist failures, plus anything that left the feed unexpectedly.
2. **New jobs:** with source and link.
3. **Changes to existing jobs:** field, old value, new value, then the postings re-checked that day and the result for each.
4. **Errors and warnings:** the routine checks (on collection runs), then file, problem and fix, plus files that were moved and any merged duplicates.
5. **Watchlist:** entries checked, and each failure with the exact error and a guessed cause.
6. **Skipped candidates:** roles the routine rejected and why, and the searches it ran.
7. **All current jobs:** first the postings in the feed that close in the next 14 days, by deadline (handy to pass on to students); then every posting that isn't archived, and whether it's in the feed.

## Adapting this for another board

Everything specific to this board is in `config/`:

1. Fork or copy the repo and delete the files in `postings/`, `watchlist/` and `logs/` (keep the `.gitkeep` files).
2. Edit `config/board.json`: the programs (code, label, folder), any combined labels, the allowed levels and regions, the feed columns, the time zone, the thresholds (stale days, stale hosts, safety-stop share, closing-soon days, re-checks per run), the off-scope word lists (`scopeFlags`) and the routine checks (`routineChecks`). Make the folders for any new programs.
3. Rewrite `config/criteria.md` for the new field, and update the searches and job boards in `ROUTINE.md`.
4. Update the allowed values in `schema/` to match.
5. Run the tests, add your first postings, and push. The Action builds the feed.
