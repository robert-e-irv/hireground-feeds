"""Offline test for the hourly tier: body cache, due-based refresh, 304s, and export from the cache.

A fake server plays three boards:
  greenhouse:studio   games studio, ETag + 304 support (refreshed hourly)
  smartrecruiters:pub games publisher, paginated, no validators (hourly)
  lever:employer      non-games employer, no validators (every 6h)

Run:  python tests/test_cache.py   (or python -m pytest -q tests)
"""
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "recorder"), str(ROOT / "analysis")]
import analyze  # noqa: E402
import record   # noqa: E402

record.HOST_GAP = 0.0
T0 = datetime(2026, 11, 4, 0, 17, tzinfo=timezone.utc)
STATE = {"studio_jobs": ["Gameplay Programmer"], "hits": {}, "employer_down": False}
CLOCK = {"now": T0}
record.now_iso = lambda: CLOCK["now"].isoformat(timespec="seconds")


def iso(dt):
    return dt.isoformat()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path
        key = p.split("?")[0]
        STATE["hits"][key] = STATE["hits"].get(key, 0) + 1
        if p.startswith("/gh"):
            body = json.dumps({"jobs": [{"id": i + 1, "title": t, "absolute_url": f"https://x/{i}",
                                         "first_published": iso(T0 - timedelta(days=2))}
                                        for i, t in enumerate(STATE["studio_jobs"])]}).encode()
            etag = '"' + hashlib.md5(body).hexdigest() + '"'
            if self.headers.get("If-None-Match") == etag:
                return self.send(304, headers={"ETag": etag})
            return self.send(200, body, {"ETag": etag, "Content-Type": "application/json"})
        if p.startswith("/sr"):
            off = int(p.split("offset=")[1]) if "offset=" in p else 0
            items = [{"id": f"s{i}", "name": f"Publisher Job {i}", "ref": f"https://sr/{i}",
                      "releasedDate": iso(T0 - timedelta(days=1))} for i in range(off, min(off + 2, 3))]
            return self.send(200, json.dumps({"offset": off, "limit": 2, "totalFound": 3,
                                              "content": items}).encode())
        if p.startswith("/lever"):
            if STATE["employer_down"]:
                return self.send(503, b"down")
            return self.send(200, json.dumps([{"id": "e1", "text": "Registered Nurse", "hostedUrl": "https://l/1",
                                               "createdAt": int((T0 - timedelta(days=1)).timestamp() * 1000)}]).encode())
        self.send(404)


def srcs(base):
    return [
        {"id": "greenhouse:studio", "name": "Moon Studio", "kind": "greenhouse", "category": "studio",
         "url": base + "/gh", "redistributable": True, "notes": ""},
        {"id": "smartrecruiters:pub", "name": "Big Publisher", "kind": "smartrecruiters", "category": "publisher",
         "url": base + "/sr?limit=2", "redistributable": True, "notes": ""},
        {"id": "lever:employer", "name": "Good Clinic", "kind": "lever", "category": "employer", "industry": "health",
         "url": base + "/lever", "redistributable": True, "notes": ""},
    ]


def latest_index(data):
    f = sorted((data / "index").glob("*/*.jsonl.gz"))[-1]
    return {r["source"]: r for r in map(json.loads, gzip.open(f, "rt").read().splitlines())}


def export(data, site, cache):
    out = subprocess.run(["node", str(ROOT / "pipeline" / "export.mjs"), "--data", str(data),
                          "--site", str(site), "--cache", str(cache)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads((site / "jobs.json").read_text())


def go(data, cache, base, hours, force=False):
    CLOCK["now"] = T0 + timedelta(hours=hours)
    rid = CLOCK["now"].strftime("%Y%m%dT%H%M%SZ")
    s = record.run(data, srcs(base), run_id=rid, cache=cache, force=force, now=CLOCK["now"])
    return s, latest_index(data)


def test_hourly_tier():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    tmp = Path(tempfile.mkdtemp(prefix="hg-cache-"))
    data, cache, site = tmp / "data", tmp / "cache", tmp / "site"
    site.mkdir(parents=True)
    shutil.copy(ROOT / "site" / "index.html", site / "index.html")
    try:
        # Run 0, cold cache: everything is due and fetched.
        s, idx = go(data, cache, base, 0)
        assert s["attempted"] == 3 and s["carried"] == 0 and s["ok"] == 3, s
        assert (cache / "smartrecruiters_pub.p2.body.gz").exists(), "every page is cached"

        # Run 1 (+1h): games boards due, the employer is carried. The studio is unchanged -> 304.
        s, idx = go(data, cache, base, 1)
        assert s["attempted"] == 2 and s["carried"] == 1 and s["not_modified"] == 1, s
        st = idx["greenhouse:studio"]
        assert st["status"] == 304 and st["ok"] and st["bytes"] == 0 and st["job_count"] == 1
        assert st["revalidate"]["status"] == 304
        emp = idx["lever:employer"]
        assert emp["carried"] and "jobs" not in emp and "headers" not in emp, "carried records stay small"
        out = export(data, site, cache)
        titles = {j["title"] for j in out["jobs"]}
        assert {"Gameplay Programmer", "Registered Nurse", "Publisher Job 0", "Publisher Job 2"} <= titles, titles
        nurse = next(j for j in out["jobs"] if j["title"] == "Registered Nurse")
        assert nurse["company"] == "Good Clinic" and nurse["industry"] == "health", "carried employer keeps its tags"
        assert all(f["ok"] for f in out["feeds"]), out["feeds"]

        # Run 2 (+2h): the studio posts a new role -> 200, cache updated, new job published.
        STATE["studio_jobs"].append("Level Designer")
        s, idx = go(data, cache, base, 2)
        assert idx["greenhouse:studio"]["status"] == 200 and idx["greenhouse:studio"]["job_count"] == 2
        assert "revalidate" not in idx["greenhouse:studio"], "a changed body says nothing about 304 support"
        out = export(data, site, cache)
        assert "Level Designer" in {j["title"] for j in out["jobs"]}

        # Run 3 (+5h40m, an early-starting run): the 6h employer counts as due (30 min slack).
        hits = STATE["hits"].get("/lever", 0)
        s, idx = go(data, cache, base, 5 + 40 / 60)
        assert not idx["lever:employer"].get("carried") and STATE["hits"]["/lever"] == hits + 1

        # Run 4 (+12h): the employer fails. Not touched in the cache, so it's retried next hour.
        STATE["employer_down"] = True
        s, idx = go(data, cache, base, 12)
        assert idx["lever:employer"]["ok"] is False
        STATE["employer_down"] = False
        s, idx = go(data, cache, base, 13)
        assert idx["lever:employer"]["ok"] is True and not idx["lever:employer"].get("carried"), "retried"

        # --all refreshes everything regardless of schedule.
        s, idx = go(data, cache, base, 13.5, force=True)
        assert s["carried"] == 0 and s["attempted"] == 3

        # A partial cache restore (meta without its body) counts as missing -> fetched again.
        (cache / "lever_employer.body.gz").unlink()
        s, idx = go(data, cache, base, 14)
        assert not idx["lever:employer"].get("carried")

        # Losing the whole cache is a full refresh, never a site with missing boards.
        shutil.rmtree(cache)
        s, idx = go(data, cache, base, 15)
        assert s["carried"] == 0 and s["attempted"] == 3
        out = export(data, site, cache)
        assert len(out["feeds"]) == 3 and all(f["ok"] for f in out["feeds"])

        # The analyzer ignores carried rows (nothing was measured for them).
        by = analyze.load(data)
        assert all(not r.get("carried") for v in by.values() for r in v)
        assert len(by["lever:employer"]) < len(by["greenhouse:studio"])
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_hourly_tier()
    print("ALL PASSED")
