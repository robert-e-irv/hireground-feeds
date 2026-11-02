# Hire Ground: automated refresh pipeline (evidence-based design)

**Status:** measurement phase. Anything marked **[DATA]** is decided by `analysis/analyze.py` after the 14-day study, not by guesswork. Everything else is fixed now.

---

## 1. The study (what we measure, and why this way)

| What | How |
|---|---|
| Sources | All 76 the app uses: 69 boards across 67 studios (Greenhouse 47, Lever 13, Ashby 8, SmartRecruiters 1) + 7 aggregators |
| Frequency | Every **6 h**, not daily. You can only prove daily is fast enough by sampling faster than daily. |
| Duration | 14 days, which is about 56 runs. Stop the schedule after that, or leave it on as the production run. |
| Recorded per source | status, all response headers, time to first byte, total time, bytes, SHA-256 of body, raw body (gzipped), first-attempt failure (before retry), pages fetched, parsed job index (id, title, url, posted, updated), schema errors |
| Conditional test | If the response carried an `ETag` or `Last-Modified`, an immediate re-GET with `If-None-Match` / `If-Modified-Since`. A 304 means the source really supports cheap polling. |
| Where | GitHub Actions, the same place production would run, so the **scheduler itself is also under test** (delay, missed slots). |
| Storage | Job index + run log committed to the repo (small, and the daily commit keeps the scheduler from going dormant). Raw bodies uploaded as workflow artifacts, kept 30 days. |
| Politeness | 1 s gap per host, one retry on 429/5xx honoring `Retry-After`, identifying User-Agent. |

## 2. Decision rules (applied per source by the analyzer)

**Freshness target**
- `target = clamp(5% of median posting lifetime, 6 h, 24 h)`.
  Plain version: if roles at a studio typically stay up 20 days, being 24 h late costs 5% of the window, which is fine. If they're gone in 4 days, we need about 6 h.
- Sources with fewer than 0.1 new jobs/day get a 24 h target regardless.
- Lifetime unknown (nothing closed in-window): 24 h.

**Cadence** = the slowest of 24 h / 12 h / 6 h that meets the target.

**Cost check:** a sub-daily cadence on a source with no 304 support and bodies over 200 KB gets flagged, since every poll is a full download.

**Fix first:** below 80% success, or any schema error, means no cadence until fixed.

**Retry flag:** first-attempt failure above 20% means it needs backoff in production.

**Global checks [DATA]**
- GitHub scheduler: if missed slots exceed 2% or median delay exceeds 60 min, GitHub cron is not a trustworthy clock (see section 7).
- If more than 80% of sources land on 24 h, run the whole pipeline daily and skip per-source scheduling. It's simpler.

## 3. Pipeline (production)

```
cron tick
  └─ for each source due (per its cadence)
       fetch ── conditional GET if source honors 304, else full GET
         │      304 or same body hash ─► mark "checked, unchanged", stop
         ▼
       validate (parsers.py, strict) ── fail ─► keep last good data, raise alert
         ▼
       diff by (source, job_id) ─► new / still open / missing
         ▼
       SQLite upsert (first_seen, last_seen, closed_at)
         ▼
       health checks ── fail ─► hold this source's previous export, alert
  └─ export jobs.json (redistributable sources only) ─► deploy static site
  └─ ping dead-man's switch
```

**Incremental fetching.** Store each source's last `ETag`, `Last-Modified`, and body hash. Use the validators where the study shows they work, and the hash short-circuit everywhere else. That skips parsing and diffing when nothing changed.

**Closing a job.** Mark it closed only after it's missing from **N consecutive successful** fetches. N defaults to 2 and is set from the study's `flapping_ids` **[DATA]**. A failed fetch or schema error never closes anything (stale-but-safe beats false "closed").

## 4. SQLite cache (`hireground.db`, committed or stored as an artifact)

```sql
CREATE TABLE sources (
  id TEXT PRIMARY KEY, name TEXT, kind TEXT, url TEXT,
  redistributable INTEGER, cadence_h INTEGER,
  etag TEXT, last_modified TEXT, body_sha TEXT,
  last_attempt_at TEXT, last_ok_at TEXT, consecutive_failures INTEGER DEFAULT 0
);
CREATE TABLE jobs (
  source_id TEXT, job_id TEXT,
  title TEXT, url TEXT, location TEXT, remote TEXT, description TEXT,
  family TEXT, seniority TEXT,                -- from the existing normalizer
  posted_at TEXT, source_updated_at TEXT,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  missing_streak INTEGER DEFAULT 0, closed_at TEXT,
  content_sha TEXT,
  PRIMARY KEY (source_id, job_id)
);
CREATE TABLE fetches (            -- one row per fetch: the health history
  source_id TEXT, at TEXT, status INTEGER, ms INTEGER, bytes INTEGER,
  not_modified INTEGER, job_count INTEGER, schema_error TEXT
);
CREATE INDEX jobs_open ON jobs(closed_at) WHERE closed_at IS NULL;
```

## 5. Schema validation

`recorder/parsers.py` already fails loudly when a source drops a required field or changes shape (tested). Production reuses it and extends the job model with pydantic for the full posting (location, description, compensation).

## 6. Health checks (per feed, every run)

| Check | Rule | Action |
|---|---|---|
| Count collapse | job count < 50% of its 7-day median | hold previous data, alert |
| Unexpected empty | 0 jobs where median > 5 | hold, alert |
| Schema drift | parser raises | hold, alert |
| Repeated failure | 3 consecutive failed fetches | alert (once) |
| Stale source | `last_ok_at` older than 3 x cadence | flag source as stale in jobs.json |

Alerts open a GitHub Issue from the workflow (free, emailed to you, deduplicated by title).

## 7. Scheduler and host

**Default: GitHub Actions + GitHub Pages.** It costs $0 and there's no server to patch.
- Pages limits fit easily: sites up to 1 GB, a soft bandwidth limit of 100 GB/month. A gzipped `jobs.json` of about 1 to 2 MB allows roughly 50,000+ page loads a month.
- Pages is not allowed for commercial SaaS. If Hire Ground ever charges money, move the static files to Cloudflare Pages (same build, free tier).

**Switch to Fly.io (about $2 to 5/month) only if [DATA]:**
- GitHub misses more than 2% of slots or runs more than 60 min late, or
- any source needs a cadence under 6 h, or
- we add accounts, server-side saved searches, or email digests.

Rejected: Cloudflare Workers cron (the free tier's 10 ms CPU and subrequest cap don't fit 76 feeds), and Claude scheduled tasks (no network path to the ATS APIs).

## 8. Terms gate (what's allowed in the public cache)

| Source group | Public cache | Why |
|---|---|---|
| Greenhouse, Lever, Ashby, SmartRecruiters job-board APIs | **Yes** | Official public endpoints built for embedding a company's own listings. Always link to the original posting and honor opt-out requests. |
| Remotive, We Work Remotely, Adzuna | **No** | Terms restrict republishing; measured only |
| Arbeitnow, Jobicy, Himalayas, RemoteOK | **Not yet** | Terms unreviewed; RemoteOK requires attribution |

The `redistributable` flag in `recorder/sources.py` is the single switch. The exporter refuses anything not `True`. Non-redistributable sources can still be fetched **in the user's own browser or local app** for their personal use.

## 9. `jobs.json` (what the front end loads)

```json
{
  "generated_at": "2026-11-16T06:17:00Z",
  "sources": [{"id": "greenhouse:riotgames", "name": "Riot Games",
               "last_ok_at": "...", "stale": false}],
  "jobs": [{"k": "greenhouse:riotgames:123", "t": "Senior Game Designer",
            "c": "Riot Games", "u": "https://...", "loc": "...", "rm": "remote",
            "fam": "Design", "sen": "Senior", "posted": "...",
            "first_seen": "2026-11-15T12:17:00Z", "desc": "first ~600 chars"}],
  "closed_recent": ["greenhouse:riotgames:98", "..."]
}
```

- Short keys and trimmed descriptions keep it small. The full description loads on card expand via the posting URL.
- `closed_recent` = ids closed in the last 14 days, so saved jobs can show "closed."
- Ranking stays **in the browser**. The resume never leaves the user's device.

## 10. Validation (what proves it works, independent of us)

| Proof | Method | Pass bar |
|---|---|---|
| **Freshness lag** | `first_seen - posted_at` for every new job with a source timestamp | median under the source's target; p90 under 2x target |
| **Precision** (listed jobs really open) | Weekly: random 20 jobs from `jobs.json`, open each URL | at least 95% still open |
| **Recall** (open jobs actually listed) | Weekly: 5 random studios, count roles on their live careers page vs ours | at least 95% matched |
| **Missed runs** | Healthchecks.io dead-man's switch, period = cadence + 2 h grace | zero unalerted misses in 30 days |
| **Feed breaks** | Health-check issues vs breaks found by spot checks | every break found by a human was already alerted |
| **Terms** | Each public source has its terms text linked in `sources.py` | 100% |

Precision and recall checks are a 15-minute weekly ritual. A small script can pre-pick the samples and log results to `validation/YYYY-WW.md`.

## 11. Feature: "New matches since your last visit"

This feature only exists because the pipeline records `first_seen`.

- **Client state (localStorage, per browser):** `last_visit_at`, saved job keys, dismissed keys.
- **On load:** `new = jobs where first_seen > last_visit_at and fit >= user's threshold`.
- **UI:**
  - A header pill: "**4 new matches** since Thu."
  - A "New" badge on those cards, plus a **New** sort/filter.
  - Saved jobs found in `closed_recent` show "Closed" with the date.
  - A footer line: "Updated 6 h ago · 61 of 63 sources healthy" (from `generated_at` + `sources`).
- **Update `last_visit_at` when the user leaves**, not on load. Otherwise a refresh wipes the badges before they're seen.
- **Later (needs Fly + accounts):** a daily email or push digest of new matches, using the same query server-side.
