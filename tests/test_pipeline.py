"""Offline end-to-end test: a fake job-board server, the real recorder, the real analyzer.

The fake server mimics the behaviours we need to measure:
  - Greenhouse-shaped board with ETag + 304 support, jobs added/closed over time
  - Lever-shaped board with no validators (never 304s)
  - a flaky board (first attempt 500, retry OK)
  - SmartRecruiters-style pagination
  - an RSS feed
  - a board that changes schema mid-study
Run:  python -m pytest -q tests   (or python tests/test_pipeline.py)
"""
import gzip
import hashlib
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "recorder"), str(ROOT / "analysis")]
import record   # noqa: E402
import analyze  # noqa: E402
import parsers  # noqa: E402

record.HOST_GAP = 0.0
STATE = {"run": 0, "flaky_hits": 0}
T0 = datetime(2026, 11, 2, 0, 17, tzinfo=timezone.utc)


def gh_body():
    k = STATE["run"]
    # job 1 always open; job 2 closes at run 4; a new job appears every 2 runs
    jobs = [{"id": 1, "title": "Senior Game Designer", "absolute_url": "u1",
             "first_published": (T0 - timedelta(days=30)).isoformat(),
             "updated_at": T0.isoformat()}]
    if k < 4:
        jobs.append({"id": 2, "title": "Producer", "absolute_url": "u2",
                     "first_published": (T0 - timedelta(days=10)).isoformat()})
    for n in range(k // 2):
        posted = T0 + timedelta(hours=12 * n + 1)
        jobs.append({"id": 100 + n, "title": f"Role {n}", "absolute_url": f"n{n}",
                     "first_published": posted.isoformat()})
    return json.dumps({"jobs": jobs, "meta": {"total": len(jobs)}}).encode()


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
        if p.startswith("/gh"):
            body = gh_body()
            etag = '"' + hashlib.md5(body).hexdigest() + '"'
            if self.headers.get("If-None-Match") == etag:
                return self.send(304, headers={"ETag": etag})
            return self.send(200, body, {"ETag": etag, "Content-Type": "application/json"})
        if p.startswith("/lever"):
            return self.send(200, json.dumps([{"id": "a", "text": "Designer",
                                               "createdAt": 1761955200000}]).encode())
        if p.startswith("/flaky"):
            STATE["flaky_hits"] += 1
            if STATE["flaky_hits"] % 2 == 1:
                return self.send(500, b"oops")
            return self.send(200, json.dumps([{"id": "f", "text": "QA"}]).encode())
        if p.startswith("/sr"):
            off = int(p.split("offset=")[1]) if "offset=" in p else 0
            items = [{"id": f"s{i}", "name": f"Job {i}"} for i in range(off, min(off + 2, 5))]
            return self.send(200, json.dumps({"offset": off, "limit": 2, "totalFound": 5,
                                              "content": items}).encode())
        if p.startswith("/rss"):
            return self.send(200, b"<rss><channel><item><guid>g1</guid><title>Dev</title>"
                                  b"<pubDate>Mon, 02 Nov 2026 00:00:00 GMT</pubDate></item>"
                                  b"</channel></rss>")
        if p.startswith("/drift"):
            if STATE["run"] >= 3:
                return self.send(200, json.dumps({"postings": []}).encode())
            return self.send(200, json.dumps({"jobs": [{"id": 9, "title": "x"}]}).encode())
        self.send(404)


def start():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def srcs(base):
    mk = lambda id_, kind, path, red=True: {"id": id_, "name": id_, "kind": kind,
                                            "category": "studio", "url": base + path,
                                            "redistributable": red, "notes": ""}
    return [mk("greenhouse:fake", "greenhouse", "/gh"),
            mk("lever:fake", "lever", "/lever"),
            mk("lever:flaky", "lever", "/flaky"),
            mk("smartrecruiters:fake", "smartrecruiters", "/sr?limit=2"),
            mk("wwr", "wwr", "/rss", False),
            mk("greenhouse:drift", "greenhouse", "/drift")]


def test_parsers_reject_drift():
    try:
        parsers.parse("greenhouse", b'{"postings": []}')
        assert False, "should raise"
    except parsers.SchemaError:
        pass
    assert parsers._iso(1761955200000) == "2025-11-01T00:00:00+00:00"
    assert parsers._iso("Mon, 02 Nov 2026 00:00:00 GMT") == "2026-11-02T00:00:00+00:00"


def test_end_to_end(tmp_path=None):
    tmp = Path(tmp_path or "/tmp/hg_test")
    if tmp.exists():
        import shutil; shutil.rmtree(tmp)
    srv, base = start()
    try:
        for k in range(8):  # 8 runs, 6 h apart = 2 days
            STATE["run"] = k
            rid = (T0 + timedelta(hours=6 * k)).strftime("%Y%m%dT%H%M%SZ")
            s = record.run(tmp, srcs(base), run_id=rid)
            assert s["attempted"] == 6
    finally:
        srv.shutdown()

    # Recorder output checks
    idx = sorted((tmp / "index").glob("*/*.jsonl.gz"))
    assert len(idx) == 8
    first = [json.loads(l) for l in gzip.open(idx[0], "rt")]
    by = {r["source"]: r for r in first}
    assert by["greenhouse:fake"]["revalidate"]["status"] == 304
    assert "status" not in by["lever:fake"]["revalidate"]      # no validators -> no re-GET
    assert by["lever:flaky"]["ok"] and by["lever:flaky"]["first_attempt"]["status"] == 500
    assert by["smartrecruiters:fake"]["pages"] == 3 and by["smartrecruiters:fake"]["job_count"] == 5
    assert by["wwr"]["job_count"] == 1
    assert (tmp / "raw" / first[0]["run"]).exists()

    # Analyzer checks
    data = analyze.load(tmp)
    m = {k: analyze.analyze_source(v, 24) for k, v in data.items()}
    g = m["greenhouse:fake"]
    assert g["ok_pct"] == 100 and g["honours_304_pct"] == 100
    assert g["closed_per_day"] > 0 and g["new_per_day"] > 0
    assert g["lag_h_median"] is not None and g["lag_h_median"] >= 0
    assert g["cadence_h"] in (6, 12, 24)
    assert m["lever:fake"]["honours_304_pct"] is None
    assert m["lever:fake"]["jobset_change_pct"] == 0
    assert m["lever:flaky"]["first_attempt_fail_pct"] == 100
    assert m["greenhouse:drift"]["verdict"].startswith("FIX FIRST")

    analyze.report(list(m.values()), analyze.scheduler_stats(tmp), tmp / "report")
    rep = (tmp / "report" / "report.md").read_text()
    sched = analyze.scheduler_stats(tmp)
    assert sched["missed_slots"] == 0 and sched["runs"] == 8
    assert "greenhouse:fake" in rep
    print(rep)


if __name__ == "__main__":
    test_parsers_reject_drift()
    test_end_to_end()
    print("ALL PASSED")
