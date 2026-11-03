"""Check every candidate employer board (employers.py) and keep only the ones that are real.

Kept = HTTP 200, parses with the strict parser, has at least one open job, and (Greenhouse only)
the board's own name matches the company we expect, so a short slug like "ro" can't quietly
publish a different company's jobs.

Two candidate sets, same rules:
  employers  recorder/employers.py           -> recorder/employers_verified.json
  studios    recorder/studio_candidates.json -> recorder/studios_verified.json
             (games companies imported from Outscal's OpenJobs list by import_openjobs.py)

sources.py reads the verified files. Runs in GitHub Actions before the snapshot; skips itself when
the file is younger than --max-age-days.

  python recorder/verify_boards.py                 # verify employers now
  python recorder/verify_boards.py --set studios --max-age-days 7
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import employers  # noqa: E402
import parsers    # noqa: E402
import record     # noqa: E402  (reuses fetch + polite per-host pacing)
from sources import ATS_URL  # noqa: E402

OUT = HERE / "employers_verified.json"
SETS = {
    "employers": (None, HERE / "employers_verified.json"),
    "studios": (HERE / "studio_candidates.json", HERE / "studios_verified.json"),
}


def studio_candidates(path=SETS["studios"][0]):
    """studio_candidates.json -> verifier tuples: (name, None, boards, {"adjacent": bool})."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [(c["name"], None, [tuple(b) for b in c["boards"]], {"adjacent": bool(c.get("adjacent"))})
            for c in doc.get("candidates", [])]
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


def verify(candidates=None, fetch=record.fetch, gap=record.HOST_GAP, workers=1):
    """Candidates are (name, industry, boards) or (name, industry, boards, extra-fields).
    workers > 1 checks several companies at once; the shared Pacer still spaces requests per host."""
    pacer = record.Pacer(gap)

    def one(cand):
        name, industry, boards = cand[:3]
        extra = cand[3] if len(cand) > 3 else {}
        kept, rejected = [], []
        for ats, slug in boards:
            try:
                ok, info = check(name, ats, slug, pacer, fetch)
            except Exception as e:  # one odd board must never sink the weekly check
                ok, info = False, {"ats": ats, "slug": slug, "reason": f"{type(e).__name__}: {e}"[:200]}
            info.update(name=name, industry=industry, **extra)
            (kept if ok else rejected).append(info)
            if ok:
                break  # one live board per company is enough; the first listed wins
        return kept, rejected

    cands = list(candidates if candidates is not None else employers.CANDIDATES)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = list(ex.map(one, cands))   # map keeps the input order
    kept = [k for ks, _ in results for k in ks]
    rejected = [r for _, rs in results for r in rs]
    return {"verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kept": kept, "rejected": rejected}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=0, help="skip if the file is younger than this")
    ap.add_argument("--set", choices=sorted(SETS), default="employers")
    ap.add_argument("--out", help="default: recorder/<set>_verified.json")
    ap.add_argument("--workers", type=int, default=None, help="companies checked at once (default 1 employers, 8 studios)")
    a = ap.parse_args()
    out = Path(a.out) if a.out else SETS[a.set][1]
    if a.max_age_days and out.exists():
        try:
            at = datetime.fromisoformat(json.loads(out.read_text())["verified_at"])
            if datetime.now(timezone.utc) - at < timedelta(days=a.max_age_days):
                print(json.dumps({"skipped": "fresh", "verified_at": at.isoformat()}))
                return
        except (ValueError, KeyError):
            pass
    t0 = time.monotonic()
    if a.set == "studios":
        res = verify(studio_candidates(), workers=a.workers or 8)
    else:
        res = verify(workers=a.workers or 1)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps({"kept": len(res["kept"]), "rejected": len(res["rejected"]),
                      "jobs": sum(k.get("jobs", 0) for k in res["kept"]),
                      "wall_s": round(time.monotonic() - t0, 1)}))


if __name__ == "__main__":
    main()
