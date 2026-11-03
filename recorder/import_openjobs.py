"""Import games-company job boards from Outscal's OpenJobs dataset (the list behind ASGC's jobs workbook).

OpenJobs (https://github.com/outscal/OpenJobs, MIT) maps ~12K gaming/tech companies to their ATS.
This keeps the gaming companies whose boards sit on an ATS Hire Ground can publish
(Greenhouse, Lever, Ashby, SmartRecruiters), drops boards we already track, and writes
recorder/studio_candidates.json.

These are CANDIDATES. verify_boards.py --set studios checks them weekly in GitHub Actions and only
live boards with open jobs (and, on Greenhouse, a matching board name) reach the site.

  python recorder/import_openjobs.py                       # download the dataset from GitHub
  python recorder/import_openjobs.py --dataset companies_v2.json
  python recorder/import_openjobs.py --include-igaming     # keep casino / betting companies
"""
import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import employers  # noqa: E402
import sources    # noqa: E402

DATASET_URL = "https://raw.githubusercontent.com/outscal/OpenJobs/main/data/companies_v2.json"
OUT = HERE / "studio_candidates.json"

# Preferred order when a company lists several boards: the verifier keeps the first live one.
ATS_ORDER = ["greenhouse", "ashby", "lever", "smartrecruiters"]

# URL -> (ats, slug). Only ATSs whose terms let the public site republish (sources.ATS_REDISTRIBUTABLE).
PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?[^#]*\bfor=([A-Za-z0-9_\-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_\-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_\-]+)", re.I)),
    ("lever", re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([A-Za-z0-9_\-.]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-.%]+)", re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_\-.%]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_\-]+)", re.I)),
    ("smartrecruiters", re.compile(r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_\-]+)", re.I)),
]
# Path segments that are part of the ATS's own site, not a company slug.
NOT_SLUGS = {"embed", "jobs", "job_board", "v1", "boards", "postings", "v0", "api", "careers", "search",
             "oneclick-ui", "company", "posting-api", "job-board", "static", "assets", "sitemap", "en"}
IGAMING = re.compile(r"\b(casino|betting|bet|sportsbook|gambl\w*|igaming|i-gaming|slots?|poker|lottery|bingo|wager\w*)\b", re.I)
# Other ATSs the dataset knows but Hire Ground doesn't publish yet; reported so you can see the gap.
OTHER_ATS = {"workable": "workable.com", "workday": "myworkdayjobs.com", "teamtailor": "teamtailor.com",
             "personio": "jobs.personio", "recruitee": "recruitee.com", "bamboohr": "bamboohr.com",
             "breezy": "breezy.hr", "jobvite": "jobvite.com", "join": "join.com",
             "linkedin (no API)": "linkedin.com", "wellfound (no API)": "wellfound.com"}


def parse_board(url):
    """An ATS link -> (ats, slug), or None if it isn't a board Hire Ground can read."""
    u = str(url or "").strip()
    for ats, rx in PATTERNS:
        m = rx.search(u)
        if m:
            slug = m.group(1).strip(".")   # kept URL-encoded ("Pocket%20Worlds") so it drops into ATS_URL as-is
            if slug and slug.lower() not in NOT_SLUGS:
                return ats, slug
    return None


# The dataset's "gaming" tag and its auto-matched genres (similarity 0) are noisy: a B2B marketing firm
# can be tagged "gaming / Arcade". A company only counts as a full game studio (every role, accountants
# included, shows under Games) when something concrete says so. Everyone else is imported as
# game-adjacent, like Discord or Twitch in the app: published, but only game-titled roles count as games.
GAME_WORDS = re.compile(r"(game|games|gaming|studio|studios|interactive|play|entertainment|pixel|arcade|esports?)", re.I)
ENGINES = {"unity", "unreal", "unreal engine", "godot", "cocos", "cocos2d", "gamemaker", "defold", "phaser"}


def game_evidence(c):
    if GAME_WORDS.search(str(c.get("name", ""))) or GAME_WORDS.search(str(c.get("website", ""))):
        return True
    if {str(t).strip().lower() for t in (c.get("tech_stack") or [])} & ENGINES:
        return True
    # genres typed in by a person are plain strings; auto-matched ones are dicts with a similarity score
    return any(isinstance(g, str) or (isinstance(g, dict) and (g.get("similarity") or 0) > 0.5)
               for g in (c.get("game_genre") or []))


def is_igaming(c):
    text = " ".join([str(c.get("name", "")), str(c.get("type", ""))] +
                    [g if isinstance(g, str) else json.dumps(g) for g in (c.get("game_genre") or [])])
    return bool(IGAMING.search(text))


def name_key(name):
    s = re.sub(r"\([^)]*\)", " ", str(name or "").lower())
    s = re.sub(r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|the)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def nice_name(name):
    """'bad robot games' -> 'Bad Robot Games', 'ARENANET' -> 'Arenanet'; mixed case is left alone."""
    n = str(name or "").strip()
    return n.title() if n and (n == n.lower() or (n == n.upper() and len(n) > 4)) else n


def known():
    """Boards and company names Hire Ground already records: studios, extra boards, employer candidates."""
    boards, names = set(), set()
    for name, _cat, bs, _n in sources.STUDIOS + sources.EXTRA_BOARDS:
        boards.update(f"{a}:{s}".lower() for a, s in bs)
        names.add(name_key(name))
    for name, _ind, bs in employers.CANDIDATES:
        boards.update(f"{a}:{s}".lower() for a, s in bs)
        names.add(name_key(name))
    return boards, names


def build(companies, include_igaming=False):
    seen, seen_names = known()
    stats = Counter()
    other = Counter()
    out = []
    claimed = set()   # one company per board: the dataset has some duplicate rows
    for c in companies:
        if str(c.get("industry_category") or "").lower() != "gaming":
            continue
        stats["gaming companies"] += 1
        if not include_igaming and is_igaming(c):
            stats["skipped: casino / betting"] += 1
            continue
        boards = []
        for link in (c.get("ats_links") or []) + (c.get("list_urls") or []):
            b = parse_board(link)
            if b and b not in boards:
                boards.append(b)
            elif not b:
                for k, dom in OTHER_ATS.items():
                    if dom in str(link).lower():
                        other[k] += 1
                        break
        if not boards:
            continue
        boards.sort(key=lambda b: ATS_ORDER.index(b[0]))
        ids = {f"{a}:{s}".lower() for a, s in boards}
        if ids & seen or name_key(c.get("name")) in seen_names:
            stats["skipped: already tracked"] += 1
            continue
        if ids & claimed:
            stats["skipped: duplicate row"] += 1
            continue
        claimed |= ids
        name = nice_name(c.get("name")) or boards[0][1]
        kind = str(c.get("type") or "").lower()
        adjacent = kind not in ("gaming", "") or not game_evidence(c)
        out.append({"name": name, "adjacent": adjacent,
                    "website": c.get("website") or "", "boards": [list(b) for b in boards]})
        stats["candidates"] += 1
    out.sort(key=lambda r: r["name"].lower())
    for r in out:
        stats[f"first board on {r['boards'][0][0]}"] += 1
        stats["game-adjacent (only game-titled roles count as games)" if r["adjacent"] else "full game studios"] += 1
    return out, stats, other


def load(dataset):
    if dataset:
        return json.loads(Path(dataset).read_text(encoding="utf-8"))
    req = urllib.request.Request(DATASET_URL, headers={"User-Agent": "hireground-feeds"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", help="local companies_v2.json (default: download from GitHub)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--include-igaming", action="store_true", help="keep casino / betting / poker companies")
    a = ap.parse_args()
    companies = load(a.dataset)
    cands, stats, other = build(companies, a.include_igaming)
    doc = {"source": "Outscal OpenJobs data/companies_v2.json (MIT), https://github.com/outscal/OpenJobs",
           "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "note": "Candidates only. verify_boards.py --set studios decides what is published.",
           "candidates": cands}
    Path(a.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"written": a.out, **dict(stats),
                      "not imported (other ATS links)": dict(other.most_common())}, indent=1))


if __name__ == "__main__":
    main()
