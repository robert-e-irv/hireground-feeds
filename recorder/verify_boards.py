"""Check every candidate employer board (employers.py) and keep only the ones that are real.

Kept = HTTP 200, parses with the strict parser, has at least one open job, and (Greenhouse only)
the board's own name matches the company we expect, so a short slug like "ro" can't quietly
publish a different company's jobs.

Writes recorder/employers_verified.json, which sources.py reads. Runs in GitHub Actions before
the snapshot; skips itself when the file is younger than --max-age-days.

  python recorder/verify_boards.py                 # verify now
  python recorder/verify_boards.py --max-age-days 7
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import employers  # noqa: E402
import parsers    # noqa: E402
import record     # noqa: E402  (reuses fetch + polite per-host pacing)
from sources import ATS_URL  # noqa: E402

OUT = HERE / "employers_verified.json"
GH_BOARD = "https://boards-api.greenhouse.io/v1/boards/{slug}"


def fold(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def name_matches(expected, board_name):
    """True when the board's name and ours share their first real word ("Oscar Health" ~ "Oscar")."""
    stop = {"the", "inc", "llc", "ltd", "co", "health", "app"}
    a = [w for w in fold(expected).split() if w not in stop] or fold(expected).split()
    b = fold(board_name)
    # whole-word match only: "ro" must not match "Rocket Lab"
    return bool(a) and bool(b) and (a[0] in b.split() or b.replace(" ", "") == "".join(a))


def check(name, ats, slug, pacer, fetch=record.fetch):
    url = ATS_URL[ats].format(slug=slug)
    r = fetch(url, pacer)
    out = {"ats": ats, "slug": slug, "status": r["status"]}
    if r["status"] != 200 or r["error"]:
        out["reason"] = r["error"] or f"HTTP {r['status']}"
        return False, out
    try:
        jobs = parsers.parse(ats, r["body"])
    except (parsers.SchemaError, KeyError, TypeError, ValueError) as e:
        out["reason"] = f"schema: {type(e).__name__}: {e}"[:200]
        return False, out
    out["jobs"] = len(jobs)
    if not jobs:
        out["reason"] = "no open jobs"
        return False, out
    if ats == "greenhouse":
        m = fetch(GH_BOARD.format(slug=slug), pacer)
        try:
            board_name = json.loads(m["body"]).get("name", "") if m["status"] == 200 else ""
        except ValueError:
            board_name = ""
        out["board_name"] = board_name
        if not name_matches(name, board_name):
            out["reason"] = f"board name {board_name!r} doesn't match {name!r}"
            return False, out
    return True, out


def verify(candidates=None, fetch=record.fetch, gap=record.HOST_GAP):
    pacer = record.Pacer(gap)
    kept, rejected = [], []
    for name, industry, boards in (candidates or employers.CANDIDATES):
        for ats, slug in boards:
            ok, info = check(name, ats, slug, pacer, fetch)
            info.update(name=name, industry=industry)
            (kept if ok else rejected).append(info)
            if ok:
                break  # one live board per employer is enough; the first listed wins
    return {"verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kept": kept, "rejected": rejected}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=0, help="skip if the file is younger than this")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    out = Path(a.out)
    if a.max_age_days and out.exists():
        try:
            at = datetime.fromisoformat(json.loads(out.read_text())["verified_at"])
            if datetime.now(timezone.utc) - at < timedelta(days=a.max_age_days):
                print(json.dumps({"skipped": "fresh", "verified_at": at.isoformat()}))
                return
        except (ValueError, KeyError):
            pass
    t0 = time.monotonic()
    res = verify()
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({"kept": len(res["kept"]), "rejected": len(res["rejected"]),
                      "jobs": sum(k.get("jobs", 0) for k in res["kept"]),
                      "wall_s": round(time.monotonic() - t0, 1)}))


if __name__ == "__main__":
    main()
