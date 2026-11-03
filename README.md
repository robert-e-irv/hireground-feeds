# Hire Ground

**Live app:** https://robert-e-irv.github.io/hireground-feeds/

Every hour GitHub Actions refreshes the job feeds that are due (games studios and publishers hourly, everything else every 6 hours), builds `jobs.json` with the app's own parser (only sources whose terms allow republishing), and redeploys the site. A body cache carried between runs supplies the boards that weren't due, and every fetch asks "changed since last time?" so unchanged boards cost almost nothing. To refresh every board at once: Actions tab → *Refresh jobs and publish site* → **Run workflow** → tick **full**.

**Page-only changes publish themselves.** Any push to `main` that changes `site/` runs *Publish site only*: it tests the app logic, waits for any running refresh to finish, and redeploys the page with the jobs list that's already live. About a minute, no feed refresh. You can also run it by hand from the Actions tab. Visitors just open the URL. Resumes are read and ranked in the visitor's browser and never uploaded.

| Path | What |
|---|---|
| `site/index.html` | The app. Its `// <pure>` block is the shared feed parser |
| `pipeline/export.mjs` | Builds `site/jobs.json` from the latest snapshot, and tags each posting with its occupation, level, licences and remote limits (`site/relevance.js`) |
| `site/relevance.js` | Relevance rules: reads a resume into a profile the person can correct, and decides which jobs to show them. Used by the page, the pipeline and the tests |
| `site/onet_dict.json` | Job-title dictionary (O*NET 30.1, CC BY 4.0). Rebuild with `node pipeline/build_onet.mjs` after editing its title lists or updating `data/onet/` |
| `recorder/employers.py` | Non-games employers (health, finance, retail, education, nonprofit, tech) on Greenhouse, Lever, Ashby and SmartRecruiters. Candidates only |
| `recorder/import_openjobs.py` | Imports games-company boards from [Outscal's OpenJobs](https://github.com/outscal/OpenJobs) list (MIT, the data behind ASGC's jobs workbook) into `recorder/studio_candidates.json`. Candidates only |
| `recorder/sources.py` `cadence_h` | How often each kind of board is refreshed (`CADENCE_H`, `FAST_CATEGORIES`) |
| `recorder/verify_boards.py` | Weekly check in Actions: keeps boards that answer with open jobs and (Greenhouse) whose board name matches. Writes `recorder/employers_verified.json`, and with `--set studios` writes `recorder/studios_verified.json` |

### Refreshing the OpenJobs studio list

```bash
python recorder/import_openjobs.py        # re-download the list and rewrite studio_candidates.json
```

Commit the new file; the next weekly check verifies it (or delete `recorder/studios_verified.json` to force a check on the next run). Casino and betting companies are left out unless you pass `--include-igaming`. Companies without clear evidence of being a game studio are imported as game-adjacent: only their game-related roles are published.

---

## Feed snapshot study

A 14-day measurement of every Hire Ground job source, run by GitHub Actions every 6 hours. The results decide the production refresh pipeline (see `DESIGN.md`).

## Setup (about 10 minutes)

1. **Create a public GitHub repo** (e.g. `hireground-feeds`) and upload this folder's contents. Keep the `.github` folder; it's hidden on Mac, so press `Cmd+Shift+.` in Finder to see it.
2. **Allow the bot to commit:** Settings → Actions → General → Workflow permissions → **Read and write**.
3. **Dead-man's switch (free):** sign up at healthchecks.io, create a check with **Period 6 h, Grace 2 h**, and copy its ping URL. In the repo, go to Settings → Secrets and variables → Actions → New secret, name it `HEALTHCHECK_URL`, and paste the URL. You'll get an email if a run is missed or fails.
4. **Optional:** add `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` secrets. Without them, Adzuna is skipped and logged as such.
5. **Start it:** Actions tab → *Feed snapshot study* → **Run workflow**. Check that the run is green and that a `data/index/...` file was committed.

After that it runs by itself at :17 past 00, 06, 12 and 18 UTC.

## After 14 days

```bash
python analysis/analyze.py --root data --out report
```

Open `report/report.md`. It includes scheduler reliability, per-source failure rate, cost, 304 support, change rate, freshness lag, posting lifetime, and the recommended cadence for each source. `report/metrics.csv` has every column.

## Layout

| Path | What |
|---|---|
| `recorder/sources.py` | All 76 sources + the `redistributable` terms flag |
| `recorder/record.py` | The recorder (stdlib Python, no installs) |
| `recorder/parsers.py` | Strict per-source parsers (the schema check) |
| `analysis/analyze.py` | Turns snapshots into the evidence report |
| `tests/test_pipeline.py` | Offline end-to-end test against a fake job-board server |
| `data/` | Created by the workflow: `index/` (committed), `runs.jsonl` (committed), `raw/` (artifact only) |

## Run locally (optional)

```bash
python recorder/record.py --root data --only greenhouse:riotgames,lever:skyboxlabs
python tests/test_pipeline.py
python tests/test_cache.py
python tests/test_openjobs.py
python tests/test_employers.py
node tests/test_app_logic.mjs
node tests/test_relevance.mjs
node tests/test_export.mjs
```
