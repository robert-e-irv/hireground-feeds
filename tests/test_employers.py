"""Offline test for the non-games employer boards: verifier rules + how verified boards reach the source list.

Run:  python tests/test_employers.py   (or python -m pytest -q tests)
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "recorder"))
import sources        # noqa: E402
import verify_boards  # noqa: E402

GH_JOBS = json.dumps({"jobs": [{"id": 1, "title": "Registered Nurse", "absolute_url": "u",
                                "first_published": "2026-10-30T00:00:00Z"}]}).encode()
EMPTY = json.dumps({"jobs": []}).encode()


def fake_fetch(url, pacer, headers=None):
    resp = {"status": 200, "error": None, "headers": {}, "body": b""}
    if url.endswith("/boards/goodclinic/jobs?content=true"):
        resp["body"] = GH_JOBS
    elif url.endswith("/boards/goodclinic"):
        resp["body"] = json.dumps({"name": "Good Clinic Inc."}).encode()
    elif url.endswith("/boards/ro/jobs?content=true"):
        resp["body"] = GH_JOBS
    elif url.endswith("/boards/ro"):
        resp["body"] = json.dumps({"name": "Rocket Lab"}).encode()   # same slug, different company
    elif url.endswith("/boards/closedco/jobs?content=true"):
        resp["body"] = EMPTY
    elif "lever.co/v0/postings/fallbackco" in url:
        resp["body"] = json.dumps([{"id": "a", "text": "Store Manager", "hostedUrl": "h", "createdAt": 1761955200000}]).encode()
    else:
        resp.update(status=404, body=b"not found")
    return resp


def test_verifier_rules():
    res = verify_boards.verify([
        ("Good Clinic", "health", [("greenhouse", "goodclinic")]),
        ("Ro", "health", [("greenhouse", "ro")]),
        ("Closed Co", "retail", [("greenhouse", "closedco")]),
        ("Fallback Co", "retail", [("greenhouse", "nope"), ("lever", "fallbackco")]),
    ], fetch=fake_fetch, gap=0)
    kept = {k["name"]: k for k in res["kept"]}
    reasons = {r["name"] + ":" + r["slug"]: r["reason"] for r in res["rejected"]}
    assert set(kept) == {"Good Clinic", "Fallback Co"}, kept
    assert kept["Fallback Co"]["ats"] == "lever", "falls through to the next listed board"
    assert "doesn't match" in reasons["Ro:ro"], "a slug owned by another company is rejected"
    assert reasons["Closed Co:closedco"] == "no open jobs"
    assert reasons["Fallback Co:nope"] == "HTTP 404"


def test_name_match():
    assert verify_boards.name_matches("Oscar Health", "Oscar")
    assert verify_boards.name_matches("The New York Times", "The New York Times")
    assert verify_boards.name_matches("1Password", "1Password")
    assert not verify_boards.name_matches("Ro", "Rocket Lab")


def test_sources_include_verified_with_industry():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "v.json"
        p.write_text(json.dumps({"verified_at": "2026-11-02T00:00:00+00:00", "kept": [
            {"name": "Good Clinic", "industry": "health", "ats": "greenhouse", "slug": "goodclinic"},
            {"name": "Riot dup", "industry": "software", "ats": "greenhouse", "slug": "riotgames"}]}))
        orig = sources.verified_employers
        sources.verified_employers = lambda path=None: orig(p)
        try:
            srcs = {s["id"]: s for s in sources.all_sources()}
        finally:
            sources.verified_employers = orig
    s = srcs["greenhouse:goodclinic"]
    assert s["category"] == "employer" and s["industry"] == "health" and s["redistributable"] is True
    assert srcs["greenhouse:riotgames"]["category"] != "employer", "a studio board is never re-added as an employer"


if __name__ == "__main__":
    for fn in [test_verifier_rules, test_name_match, test_sources_include_verified_with_industry]:
        fn()
    print("ALL PASSED")
