"""Hire Ground raw snapshot recorder.

One run = one GET of every source, recorded exactly as received:
status, headers, timing, size, body hash, raw body (gzipped). Then, for any
response that carried an ETag or Last-Modified, an immediate conditional re-GET
to see whether the source really answers 304 Not Modified.

With --cache DIR (production): DIR keeps the latest body + validators for every source between
runs. Each run fetches only sources that are due (sources.cadence_h: games hourly, others 6h),
sends If-None-Match / If-Modified-Since so unchanged boards answer 304 with no body, and records
the rest as "carried" from the cache. A cold or missing cache makes everything due.

Outputs (paths relative to --root):
  raw/<run_id>/<source>.body.gz      raw bodies (uploaded as a CI artifact, not committed)
  index/<yyyy-mm-dd>/<run_id>.jsonl.gz   one line per source: metadata + parsed job index
  runs.jsonl                         one summary line per run (committed; keeps repo active)

Stdlib only, so it runs anywhere Python 3.10+ runs.
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parsers  # noqa: E402
import sources as sources_mod  # noqa: E402

UA = "HireGroundSnapshot/1.0 (+https://github.com/{repo}; job-feed freshness study)"
TIMEOUT = 30
HOST_GAP = 1.0          # seconds between requests to the same host
MAX_PAGES = 10
KEEP_HEADERS_DROP = {"set-cookie", "cf-ray", "report-to", "nel"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def redact(url):
    p = urllib.parse.urlsplit(url)
    q = [(k, "REDACTED" if k in ("app_id", "app_key") else v)
         for k, v in urllib.parse.parse_qsl(p.query)]
    return urllib.parse.urlunsplit(p._replace(query=urllib.parse.urlencode(q)))


def with_secrets(src):
    url = src["url"]
    if src["kind"] == "adzuna":
        aid, key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
        if not (aid and key):
            return None
        url += f"&app_id={aid}&app_key={key}"
    return url


class Pacer:
    """Polite per-host spacing."""
    def __init__(self, gap):
        self.gap, self.last, self.lock = gap, defaultdict(float), threading.Lock()

    def wait(self, host):
        with self.lock:
            delay = self.last[host] + self.gap - time.monotonic()
            self.last[host] = max(time.monotonic(), self.last[host] + self.gap)
        if delay > 0:
            time.sleep(delay)


def fetch(url, pacer, headers=None):
    """One GET. Returns dict with status, headers, body, timing. Never raises."""
    host = urllib.parse.urlsplit(url).netloc
    pacer.wait(host)
    h = {"User-Agent": UA.format(repo=os.environ.get("GITHUB_REPOSITORY", "local")),
         "Accept-Encoding": "identity"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    t0 = time.monotonic()
    out = {"status": None, "error": None, "headers": {}, "body": b"",
           "ttfb_ms": None, "elapsed_ms": None}
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        resp = e  # 304, 4xx, 5xx still carry headers and maybe a body
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:300]
        out["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
        return out
    out["ttfb_ms"] = round((time.monotonic() - t0) * 1000)
    out["status"] = resp.status if hasattr(resp, "status") else resp.code
    out["headers"] = {k.lower(): v for k, v in resp.headers.items()
                      if k.lower() not in KEEP_HEADERS_DROP}
    try:
        out["body"] = resp.read()
    except Exception as e:
        out["error"] = f"read: {type(e).__name__}: {e}"[:300]
    out["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    return out


def fetch_with_retry(url, pacer, headers=None):
    """First attempt is always recorded; one retry on 429/5xx/network error."""
    first = fetch(url, pacer, headers)
    retryable = first["error"] or (first["status"] or 0) == 429 or (first["status"] or 0) >= 500
    if not retryable:
        return first, None
    ra = first["headers"].get("retry-after", "")
    time.sleep(min(int(ra), 30) if ra.isdigit() else 5)
    return fetch(url, pacer, headers), first


def next_page(kind, url, body, page):
    """URL of the next page for paginated sources, else None."""
    try:
        d = json.loads(body)
    except ValueError:
        return None
    if kind == "smartrecruiters":
        total, off = d.get("totalFound", 0), d.get("offset", 0) + d.get("limit", 100)
        if off < total:
            p = urllib.parse.urlsplit(url)
            q = dict(urllib.parse.parse_qsl(p.query)); q["offset"] = str(off)
            return urllib.parse.urlunsplit(p._replace(query=urllib.parse.urlencode(q)))
    if kind == "arbeitnow":
        return (d.get("links") or {}).get("next")
    return None


# ---- body cache ------------------------------------------------------------------------------
CACHE_SLACK_H = 0.5   # an hourly run that starts a few minutes early still counts a 1h board as due


def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def cache_meta(cache, sid):
    if not cache:
        return None
    try:
        m = json.loads((cache / f"{safe(sid)}.meta.json").read_text())
    except (OSError, ValueError):
        return None
    # a meta whose body files are gone is useless (partial cache restore)
    if not all((cache / f).exists() for f in m.get("files", [])):
        return None
    return m


def cache_bodies(cache, meta):
    return [gzip.decompress((cache / f).read_bytes()) for f in meta["files"]]


def cache_store(cache, src, bodies, headers, rec):
    """Latest good body for a source + the validators to ask 'changed since?' next time."""
    base = safe(src["id"])
    files = [f"{base}.body.gz"] + [f"{base}.p{i}.body.gz" for i in range(2, len(bodies) + 1)]
    for f, b in zip(files, bodies):
        (cache / f).write_bytes(gzip.compress(b))
    for old in cache.glob(f"{base}.p*.body.gz"):   # fewer pages than last time
        if old.name not in files:
            old.unlink()
    meta = {"source": src["id"], "files": files, "etag": headers.get("etag"),
            "last_modified": headers.get("last-modified"), "sha256": rec.get("sha256"),
            "fetched_at": rec["at"], "checked_at": rec["at"],
            "status": rec.get("status"), "job_count": rec.get("job_count"),
            "schema_error": rec.get("schema_error"), "pages": len(bodies)}
    (cache / f"{base}.meta.json").write_text(json.dumps(meta))
    return meta


def cache_touch(cache, src, meta, when):
    meta["checked_at"] = when
    (cache / f"{safe(src['id'])}.meta.json").write_text(json.dumps(meta))


def is_due(src, meta, now, force=False):
    if force or not meta:
        return True
    last = parse_iso(meta.get("checked_at"))
    if not last:
        return True
    return (now - last).total_seconds() / 3600 >= sources_mod.cadence_h(src) - CACHE_SLACK_H


def base_rec(src, run_id):
    rec = {"run": run_id, "source": src["id"], "name": src["name"], "kind": src["kind"],
           "category": src["category"], "redistributable": src["redistributable"],
           "url": redact(src["url"]), "at": now_iso()}
    if src.get("industry"):
        rec["industry"] = src["industry"]
    if src.get("origin"):          # imported studio boards: lets export.mjs name and tag their jobs
        rec["origin"] = src["origin"]
        rec["adjacent"] = bool(src.get("adjacent"))
    return rec


def carried_rec(src, run_id, meta):
    """Not due this run: the source as of its last check, served from the cache. Small on purpose
    (no job list, no headers) because the index is committed every run."""
    rec = base_rec(src, run_id)
    rec.update(carried=True, at=meta.get("checked_at"), fetched_at=meta.get("fetched_at"),
               status=meta.get("status") or 200, ok=True, error=None,
               schema_error=meta.get("schema_error"), job_count=meta.get("job_count"),
               sha256=meta.get("sha256"), pages=meta.get("pages", 1))
    return rec


def record_source(src, run_id, root, pacer, cache=None):
    url = with_secrets(src)
    rec = base_rec(src, run_id)
    if url is None:
        rec.update(skipped="missing credentials")
        return rec

    meta = cache_meta(cache, src["id"])
    cond = {}
    if meta and meta.get("etag"):
        cond["If-None-Match"] = meta["etag"]
    if meta and meta.get("last_modified"):
        cond["If-Modified-Since"] = meta["last_modified"]
    resp, failed_first = fetch_with_retry(url, pacer, cond or None)
    body = resp["body"]

    if resp["status"] == 304 and meta and not resp["error"]:
        # Unchanged: a few hundred bytes on the wire; the cached body is the current one.
        bodies = cache_bodies(cache, meta)
        rec.update(status=304, error=None, ok=True, not_modified=True, ttfb_ms=resp["ttfb_ms"],
                   elapsed_ms=resp["elapsed_ms"], bytes=len(body), total_bytes=len(body),
                   total_elapsed_ms=resp["elapsed_ms"], sha256=meta.get("sha256"),
                   pages=meta.get("pages", 1), headers=resp["headers"],
                   first_attempt=None if failed_first is None else
                   {"status": failed_first["status"], "error": failed_first["error"],
                    "elapsed_ms": failed_first["elapsed_ms"]},
                   revalidate={"etag": bool(meta.get("etag")),
                               "last_modified": bool(meta.get("last_modified")), "status": 304})
        jobs = []
        try:
            for b in bodies:
                jobs.extend(parsers.parse(src["kind"], b))
            rec["schema_error"] = None
        except parsers.SchemaError as e:
            rec["schema_error"] = str(e)[:300]
        rec["job_count"], rec["jobs"] = len(jobs), jobs
        cache_touch(cache, src, meta, rec["at"])
        return rec
    rec.update(status=resp["status"], error=resp["error"], ttfb_ms=resp["ttfb_ms"],
               elapsed_ms=resp["elapsed_ms"], bytes=len(body),
               sha256=hashlib.sha256(body).hexdigest() if body else None,
               headers=resp["headers"],
               first_attempt=None if failed_first is None else
               {"status": failed_first["status"], "error": failed_first["error"],
                "elapsed_ms": failed_first["elapsed_ms"]})
    ok = resp["status"] == 200 and not resp["error"]
    rec["ok"] = ok

    raw_dir = root / "raw" / run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    if body:
        (raw_dir / f"{safe(src['id'])}.body.gz").write_bytes(gzip.compress(body))

    if not ok:
        return rec

    # Pagination (counted into total cost)
    bodies, pages, total_ms, total_bytes = [body], 1, resp["elapsed_ms"], len(body)
    nxt = next_page(src["kind"], url, body, 1)
    while nxt and pages < MAX_PAGES:
        r = fetch(nxt, pacer)
        if r["status"] != 200:
            rec["pagination_error"] = r["error"] or r["status"]
            break
        pages += 1; total_ms += r["elapsed_ms"]; total_bytes += len(r["body"])
        bodies.append(r["body"])
        (raw_dir / f"{safe(src['id'])}.p{pages}.body.gz").write_bytes(gzip.compress(r["body"]))
        nxt = next_page(src["kind"], nxt, r["body"], pages)
    rec.update(pages=pages, total_elapsed_ms=total_ms, total_bytes=total_bytes)

    # Parse into job index; a SchemaError is the schema-validation signal
    jobs = []
    try:
        for b in bodies:
            jobs.extend(parsers.parse(src["kind"], b))
        rec["schema_error"] = None
    except parsers.SchemaError as e:
        rec["schema_error"] = str(e)[:300]
    rec["job_count"] = len(jobs)
    rec["jobs"] = jobs

    if cache is not None:
        # Production: no second request. A 200 with validators sent and an identical body means
        # the source ignores them; a 200 with a new body says nothing (it really changed).
        if cond:
            rv = {"etag": "If-None-Match" in cond, "last_modified": "If-Modified-Since" in cond}
            if meta and rec.get("sha256") == meta.get("sha256"):
                rv["status"] = 200
                rec["revalidate"] = rv
        else:
            rec["revalidate"] = {"etag": False, "last_modified": False}
        if not rec.get("schema_error"):
            cache_store(cache, src, bodies, resp["headers"], rec)
        return rec

    # Study mode (no cache): does the source honour its own validators?
    etag, lm = resp["headers"].get("etag"), resp["headers"].get("last-modified")
    reval = {"etag": bool(etag), "last_modified": bool(lm)}
    if etag or lm:
        ch = {}
        if etag:
            ch["If-None-Match"] = etag
        if lm:
            ch["If-Modified-Since"] = lm
        r = fetch(url, pacer, ch)
        reval.update(status=r["status"], elapsed_ms=r["elapsed_ms"], bytes=len(r["body"]),
                     error=r["error"])
    rec["revalidate"] = reval
    return rec


def run(root: Path, srcs, run_id=None, workers_per_host=1, cache=None, force=False, now=None):
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pacer = Pacer(HOST_GAP)
    now = now or datetime.now(timezone.utc)
    if cache is not None:
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
    results, lock = [], threading.Lock()
    by_host = defaultdict(list)
    for s in srcs:
        meta = cache_meta(cache, s["id"]) if cache is not None else None
        if cache is not None and not is_due(s, meta, now, force):
            results.append(carried_rec(s, run_id, meta))
            continue
        by_host[urllib.parse.urlsplit(s["url"]).netloc].append(s)

    def worker(items):
        for s in items:
            try:
                r = record_source(s, run_id, root, pacer, cache)
            except Exception as e:  # never let one source kill the run
                r = {"run": run_id, "source": s["id"], "ok": False,
                     "error": f"recorder bug: {type(e).__name__}: {e}"[:300]}
            with lock:
                results.append(r)

    t0 = time.monotonic()
    threads = [threading.Thread(target=worker, args=(v,)) for v in by_host.values()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    day = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"
    idx = root / "index" / day
    idx.mkdir(parents=True, exist_ok=True)
    with gzip.open(idx / f"{run_id}.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in sorted(results, key=lambda r: r["source"]):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    attempted = [r for r in results if not r.get("skipped") and not r.get("carried")]
    summary = {"run": run_id, "at": now_iso(),
               "wall_s": round(time.monotonic() - t0, 1),
               "sources": len(results), "attempted": len(attempted),
               "ok": sum(1 for r in attempted if r.get("ok")),
               "schema_errors": sum(1 for r in attempted if r.get("schema_error")),
               "jobs": sum(r.get("job_count", 0) for r in attempted),
               "bytes": sum(r.get("total_bytes", r.get("bytes", 0)) or 0 for r in attempted),
               "carried": sum(1 for r in results if r.get("carried")),
               "not_modified": sum(1 for r in attempted if r.get("not_modified"))}
    with open(root / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="where raw/, index/, runs.jsonl go")
    ap.add_argument("--only", help="comma-separated source ids (for testing)")
    ap.add_argument("--sources-json", help="override source list (testing)")
    ap.add_argument("--cache", help="body cache dir: fetch only due sources, ask 'changed since?'")
    ap.add_argument("--all", action="store_true", help="with --cache: treat every source as due")
    a = ap.parse_args()
    srcs = (json.load(open(a.sources_json)) if a.sources_json
            else list(sources_mod.all_sources()))
    if a.only:
        keep = set(a.only.split(","))
        srcs = [s for s in srcs if s["id"] in keep]
    s = run(Path(a.root), srcs, cache=a.cache, force=a.all)
    print(json.dumps(s))
    # A run where most sources fail means the runner itself is broken:
    # exit non-zero so the dead-man's switch gets a /fail ping.
    if s["attempted"] and s["ok"] / s["attempted"] < 0.5:
        sys.exit(2)


if __name__ == "__main__":
    main()
