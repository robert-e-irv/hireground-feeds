# Hire Ground feed snapshot study

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
```
