"""Offline test for the OpenJobs studio import: link parsing, filtering, verification and the source list.

Run:  python tests/test_openjobs.py   (or python -m pytest -q tests)
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "recorder"))
import import_openjobs as imp  # noqa: E402
import sources                 # noqa: E402
import verify_boards           # noqa: E402


def test_parse_board():
    p = imp.parse_board
    assert p("https://boards.greenhouse.io/1047games") == ("greenhouse", "1047games")
    assert p("https://job-boards.greenhouse.io/cloudchamber/jobs/123") == ("greenhouse", "cloudchamber")
    assert p("https://boards.greenhouse.io/embed/job_board?for=bonfire&b=x") == ("greenhouse", "bonfire")
    assert p("https://jobs.lever.co/kabam/abc-123") == ("lever", "kabam")
    assert p("https://jobs.ashbyhq.com/Pocket%20Worlds") == ("ashby", "Pocket%20Worlds"), "kept URL-encoded"
    assert p("https://careers.smartrecruiters.com/Ubisoft2") == ("smartrecruiters", "Ubisoft2")
    assert p("https://boards.greenhouse.io/embed") is None, "an ATS path is not a company"
    assert p("https://apply.workable.com/foo") is None
    assert p("https://example.com/careers") is None


def test_build_filters_and_flags():
    data = [
        {"name": "Moon Pixel Studio", "industry_category": "gaming", "type": "gaming",
         "ats_links": ["https://jobs.lever.co/moonpixel", "https://boards.greenhouse.io/moonpixel"]},
        {"name": "Moon Pixel Studio", "industry_category": "gaming", "type": "gaming",
         "ats_links": ["https://jobs.lever.co/moonpixel"]},                                  # duplicate row
        {"name": "Acme Analytics", "industry_category": "gaming", "type": "gaming", "website": "https://acme.io",
         "game_genre": [{"label": "Arcade", "similarity": 0}], "ats_links": ["https://jobs.lever.co/acme"]},
        {"name": "Engine Folk", "industry_category": "gaming", "type": "gaming", "tech_stack": ["Unreal"],
         "ats_links": ["https://jobs.ashbyhq.com/enginefolk"]},
        {"name": "Lucky Spin Casino", "industry_category": "gaming", "type": "gaming",
         "ats_links": ["https://boards.greenhouse.io/luckyspin"]},
        {"name": "Riot Games", "industry_category": "gaming", "type": "gaming",
         "ats_links": ["https://boards.greenhouse.io/riotgames"]},                           # already tracked
        {"name": "ArenaNet", "industry_category": "gaming", "type": "gaming",
         "ats_links": ["https://boards.greenhouse.io/arenanetother"]},                       # tracked by name
        {"name": "Big Bank", "industry_category": "tech", "ats_links": ["https://boards.greenhouse.io/bigbank"]},
        {"name": "Workable Games", "industry_category": "gaming", "ats_links": ["https://apply.workable.com/wg"]},
    ]
    out, stats, other = imp.build(data)
    by = {c["name"]: c for c in out}
    assert set(by) == {"Moon Pixel Studio", "Acme Analytics", "Engine Folk"}, by
    assert by["Moon Pixel Studio"]["boards"][0] == ["greenhouse", "moonpixel"], "Greenhouse first (name check)"
    assert by["Moon Pixel Studio"]["adjacent"] is False
    assert by["Acme Analytics"]["adjacent"] is True, "an auto-tagged genre is not evidence of a game studio"
    assert by["Engine Folk"]["adjacent"] is False, "a game engine in the stack is evidence"
    assert stats["skipped: casino / betting"] == 1 and stats["skipped: duplicate row"] == 1
    assert stats["skipped: already tracked"] == 2
    assert other["workable"] == 1
    out2, _, _ = imp.build(data, include_igaming=True)
    assert "Lucky Spin Casino" in {c["name"] for c in out2}


def gh(title):
    return json.dumps({"jobs": [{"id": 1, "title": title, "absolute_url": "u",
                                 "first_published": "2026-10-30T00:00:00Z"}]}).encode()


def fake_fetch(url, pacer, headers=None):
    resp = {"status": 200, "error": None, "headers": {}, "body": b""}
    if url.endswith("/boards/moonpixel/jobs?content=true"):
        resp["body"] = gh("Gameplay Programmer")
    elif url.endswith("/boards/moonpixel"):
        resp["body"] = json.dumps({"name": "Moon Pixel Studio"}).encode()
    elif "lever.co/v0/postings/acme" in url:
        resp["body"] = json.dumps([{"id": "a", "text": "Data Engineer", "hostedUrl": "h",
                                    "createdAt": 1761955200000}]).encode()
    else:
        resp.update(status=404, body=b"not found")
    return resp


def test_verify_studios_and_sources():
    with tempfile.TemporaryDirectory() as d:
        cand = Path(d) / "c.json"
        cand.write_text(json.dumps({"candidates": [
            {"name": "Moon Pixel Studio", "adjacent": False, "boards": [["greenhouse", "moonpixel"]]},
            {"name": "Acme Analytics", "adjacent": True, "boards": [["ashby", "gone"], ["lever", "acme"]]},
            {"name": "Dead Studio", "adjacent": False, "boards": [["greenhouse", "deadstudio"]]},
        ]}))
        res = verify_boards.verify(verify_boards.studio_candidates(cand), fetch=fake_fetch, gap=0, workers=4)
        kept = {k["name"]: k for k in res["kept"]}
        assert [k["name"] for k in res["kept"]] == ["Moon Pixel Studio", "Acme Analytics"], "input order kept"
        assert kept["Acme Analytics"]["ats"] == "lever" and kept["Acme Analytics"]["adjacent"] is True
        assert {r["slug"] for r in res["rejected"]} == {"gone", "deadstudio"}

        v = Path(d) / "v.json"
        v.write_text(json.dumps(res))
        orig = sources.verified_studios
        sources.verified_studios = lambda path=None: orig(v)
        try:
            srcs = {s["id"]: s for s in sources.all_sources()}
        finally:
            sources.verified_studios = orig
    s = srcs["greenhouse:moonpixel"]
    assert s["category"] == "studio" and s["origin"] == "openjobs" and s["adjacent"] is False
    assert s["redistributable"] is True and s["name"] == "Moon Pixel Studio"
    assert srcs["lever:acme"]["adjacent"] is True


def test_shipped_candidates_are_new_and_readable():
    doc = json.loads((ROOT / "recorder" / "studio_candidates.json").read_text(encoding="utf-8"))
    tracked, _ = imp.known()
    seen = set()
    for c in doc["candidates"]:
        assert c["name"] and c["boards"], c
        for ats, slug in c["boards"]:
            assert ats in sources.ATS_URL, ats
            bid = f"{ats}:{slug}".lower()
            assert bid not in tracked, f"{bid} is already tracked"
            assert bid not in seen, f"{bid} listed twice"
            seen.add(bid)


if __name__ == "__main__":
    for fn in [test_parse_board, test_build_filters_and_flags, test_verify_studios_and_sources,
               test_shipped_candidates_are_new_and_readable]:
        fn()
        print("ok", fn.__name__)
    print("ALL PASSED")
