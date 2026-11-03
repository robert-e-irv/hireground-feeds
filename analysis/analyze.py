"""Turn two weeks of snapshots into evidence: per-source metrics + a cadence call.

Usage:  python analysis/analyze.py --root data [--target-max-h 24] [--out report]
Writes: <out>/report.md and <out>/metrics.csv
"""
import argparse
import csv
import gzip
import json
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEDULE_MIN = 17          # cron "17 * * * *" (hourly since the games tier; was every 6h)
SCHEDULE_EVERY_H = 1


def ts(run_id):
    return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def iso(s):
    return datetime.fromisoformat(s) if s else None


def pct(n, d):
    return round(100 * n / d, 1) if d else None


def med(xs):
    return round(st.median(xs), 1) if xs else None


def p90(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1)


def load(root):
    by_src = defaultdict(list)
    for f in sorted((root / "index").glob("*/*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("carried"):   # not fetched this run (hourly tier): nothing was measured
                    continue
                by_src[r["source"]].append(r)
    for v in by_src.values():
        v.sort(key=lambda r: r["run"])
    return by_src


def scheduler_stats(root):
    """How late and how often GitHub actually ran us versus the cron slots."""
    p = root / "runs.jsonl"
    if not p.exists():
        return {}
    runs = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    starts = sorted(ts(r["run"]) for r in runs)
    if not starts:
        return {}
    delays, slots_hit = [], set()
    for s in starts:
        slot = s.replace(minute=SCHEDULE_MIN, second=0, microsecond=0)
        slot = slot.replace(hour=(slot.hour // SCHEDULE_EVERY_H) * SCHEDULE_EVERY_H)
        if slot > s:
            slot -= timedelta(hours=SCHEDULE_EVERY_H)
        delays.append((s - slot).total_seconds() / 60)
        slots_hit.add(slot)
    first = min(slots_hit)
    last = max(slots_hit)
    expected = int((last - first).total_seconds() // (SCHEDULE_EVERY_H * 3600)) + 1
    gaps = [(b - a).total_seconds() / 3600 for a, b in zip(starts, starts[1:])]
    return {"runs": len(starts), "expected_slots": expected,
            "missed_slots": expected - len(slots_hit),
            "delay_min_median": med(delays), "delay_min_max": round(max(delays), 1),
            "max_gap_h": round(max(gaps), 1) if gaps else None,
            "first": starts[0].isoformat(), "last": starts[-1].isoformat(),
            "wall_s_median": med([r["wall_s"] for r in runs])}


def analyze_source(recs, target_max_h):
    att = [r for r in recs if not r.get("skipped")]
    m = {"source": recs[0]["source"], "name": recs[0].get("name"),
         "kind": recs[0].get("kind"), "redistributable": recs[0].get("redistributable"),
         "runs": len(att)}
    if not att:
        m["verdict"] = "never attempted (missing credentials?)"
        return m
    ok = [r for r in att if r.get("ok")]
    first_fail = [r for r in att if r.get("first_attempt") or not r.get("ok")]
    statuses = defaultdict(int)
    for r in att:
        statuses[str(r.get("status") or r.get("error", "err")[:20])] += 1
    m.update(ok_pct=pct(len(ok), len(att)),
             first_attempt_fail_pct=pct(len(first_fail), len(att)),
             rate_limited=sum(1 for r in att if r.get("status") == 429 or
                              (r.get("first_attempt") or {}).get("status") == 429),
             schema_error_pct=pct(sum(1 for r in ok if r.get("schema_error")), len(ok)),
             statuses=dict(statuses))

    # Fetch cost
    m.update(ms_median=med([r.get("total_elapsed_ms", r["elapsed_ms"]) for r in ok]),
             ms_p90=p90([r.get("total_elapsed_ms", r["elapsed_ms"]) for r in ok]),
             kb_median=med([r.get("total_bytes", r["bytes"]) / 1024 for r in ok]),
             pages_max=max((r.get("pages", 1) for r in ok), default=None))

    # Conditional request support
    rv = [r["revalidate"] for r in ok if r.get("revalidate")]
    with_val = [x for x in rv if x.get("etag") or x.get("last_modified")]
    m.update(etag_pct=pct(sum(1 for x in rv if x.get("etag")), len(rv)),
             last_modified_pct=pct(sum(1 for x in rv if x.get("last_modified")), len(rv)),
             honours_304_pct=pct(sum(1 for x in with_val if x.get("status") == 304),
                                 len(with_val)))

    # Change rate between consecutive good runs
    pairs = list(zip(ok, ok[1:]))
    m["body_change_pct"] = pct(sum(1 for a, b in pairs if a["sha256"] != b["sha256"]), len(pairs))
    sets = [(r, {j["id"] for j in r.get("jobs", [])}) for r in ok if not r.get("schema_error")]
    m["jobset_change_pct"] = pct(sum(1 for (a, sa), (b, sb) in zip(sets, sets[1:]) if sa != sb),
                                 len(sets) - 1 if len(sets) > 1 else 0)
    m["jobs_median"] = med([len(s) for _, s in sets])

    # Job lifecycle: first seen / last seen / closed / flapping
    if len(sets) < 2:
        m["verdict"] = "not enough good runs yet"
        return m
    baseline = sets[0][1]
    first_seen, last_seen, posted, presence = {}, {}, {}, defaultdict(list)
    for r, s in sets:
        t = ts(r["run"])
        for j in r.get("jobs", []):
            first_seen.setdefault(j["id"], t)
            last_seen[j["id"]] = t
            if j.get("posted"):
                posted.setdefault(j["id"], iso(j["posted"]))
        for jid in first_seen:
            presence[jid].append(jid in s)
    final = sets[-1][1]
    new_ids = [j for j in first_seen if j not in baseline]
    closed = [j for j in first_seen if j not in final]
    flapped = [j for j, p in presence.items()
               if any(a and not b for a, b in zip(p, p[1:])) and p[-1]]
    span_days = max((ts(sets[-1][0]["run"]) - ts(sets[0][0]["run"])).total_seconds() / 86400, 1e-9)
    m.update(new_per_day=round(len(new_ids) / span_days, 2),
             closed_per_day=round(len(closed) / span_days, 2),
             flapping_ids=len(flapped),
             posted_ts_pct=pct(sum(1 for j in first_seen if j in posted), len(first_seen)))

    # Freshness lag: how long after the source says it was posted did we first see it?
    lags = [(first_seen[j] - posted[j]).total_seconds() / 3600 for j in new_ids
            if j in posted and posted[j] <= first_seen[j]]
    m.update(lag_h_median=med(lags), lag_h_p90=p90(lags),
             lag_samples=len(lags))

    # What daily polling would have added on top of 6-hourly
    run_times = [ts(r["run"]) for r, _ in sets]
    daily = [t for t in run_times if t.hour < SCHEDULE_EVERY_H]
    extra = []
    for j in new_ids:
        later = [d for d in daily if d >= first_seen[j]]
        if later:
            extra.append((later[0] - first_seen[j]).total_seconds() / 3600)
    m["daily_extra_lag_h_median"] = med(extra)

    # How long postings stay up (only observable for ones that closed in-window)
    life = [(last_seen[j] - posted[j]).total_seconds() / 86400 for j in closed
            if j in posted and posted[j] <= last_seen[j]]
    m["lifetime_days_median"] = med(life)

    # Freshness target + cadence decision (rules documented in DESIGN.md §2)
    if m["ok_pct"] < 80 or (m["schema_error_pct"] or 0) > 0:
        m["verdict"] = "FIX FIRST: unreliable or schema drift"
        m["target_h"] = m["cadence_h"] = None
        return m
    if m["lifetime_days_median"]:
        target = min(target_max_h, max(6, 0.05 * m["lifetime_days_median"] * 24))
    else:
        target = target_max_h
    if m["new_per_day"] < 0.1:
        target = max(target, 24)
    cadence = next(c for c in (24, 12, 6) if c <= target or c == 6)
    m["target_h"], m["cadence_h"] = round(target, 1), cadence
    cheap = (m["honours_304_pct"] or 0) >= 90 or (m["kb_median"] or 0) < 200
    m["verdict"] = (f"every {cadence}h" + ("" if cheap or cadence == 24 else
                    " (no 304 support and heavy: consider 24h)")
                    + (" + needs retry/backoff" if (m["first_attempt_fail_pct"] or 0) > 20
                       else ""))
    return m


COLS = ["source", "name", "kind", "redistributable", "runs", "ok_pct",
        "first_attempt_fail_pct", "rate_limited", "schema_error_pct", "ms_median", "ms_p90",
        "kb_median", "pages_max", "etag_pct", "last_modified_pct", "honours_304_pct",
        "body_change_pct", "jobset_change_pct", "jobs_median", "new_per_day",
        "closed_per_day", "flapping_ids", "posted_ts_pct", "lag_h_median", "lag_h_p90",
        "lag_samples", "daily_extra_lag_h_median", "lifetime_days_median", "target_h",
        "cadence_h", "verdict"]


def fmt(v):
    return "" if v is None else str(v)


def report(metrics, sched, out):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for m in metrics:
            w.writerow(m)

    good = [m for m in metrics if m.get("cadence_h")]
    fix = [m for m in metrics if str(m.get("verdict", "")).startswith("FIX")]
    L = ["# Hire Ground feed snapshot report", ""]
    if sched:
        L += ["## Scheduler (is GitHub a reliable clock?)", "",
              f"- Window: {sched['first']} to {sched['last']}",
              f"- Runs: {sched['runs']} of {sched['expected_slots']} slots "
              f"(missed {sched['missed_slots']})",
              f"- Start delay vs cron: median {sched['delay_min_median']} min, "
              f"max {sched['delay_min_max']} min; longest gap {sched['max_gap_h']} h",
              f"- Run duration: median {sched['wall_s_median']} s", ""]
    L += ["## Summary", "",
          f"- Sources measured: {len(metrics)}; healthy with a cadence: {len(good)}; "
          f"need fixing: {len(fix)}",
          f"- New jobs/day across all sources: "
          f"{round(sum(m.get('new_per_day') or 0 for m in metrics), 1)}",
          f"- Sources honouring 304: "
          f"{sum(1 for m in metrics if (m.get('honours_304_pct') or 0) >= 90)}",
          "- Cadence split: " + ", ".join(
              f"{c}h: {sum(1 for m in good if m['cadence_h'] == c)}" for c in (6, 12, 24)),
          ""]
    short = ["source", "ok_pct", "ms_median", "kb_median", "honours_304_pct",
             "jobset_change_pct", "new_per_day", "lag_h_median",
             "daily_extra_lag_h_median", "lifetime_days_median", "cadence_h", "verdict"]
    L += ["## Per source", "", "| " + " | ".join(short) + " |",
          "|" + "---|" * len(short)]
    for m in sorted(metrics, key=lambda m: (m.get("cadence_h") or 0, -(m.get("new_per_day") or 0))):
        L.append("| " + " | ".join(fmt(m.get(c)) for c in short) + " |")
    L += ["", "Full columns in metrics.csv. Decision rules: DESIGN.md section 2."]
    (out / "report.md").write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data")
    ap.add_argument("--out", default="report")
    ap.add_argument("--target-max-h", type=float, default=24)
    a = ap.parse_args()
    root = Path(a.root)
    metrics = [analyze_source(v, a.target_max_h) for v in load(root).values()]
    report(metrics, scheduler_stats(root), Path(a.out))
    print(f"wrote {a.out}/report.md and {a.out}/metrics.csv ({len(metrics)} sources)")


if __name__ == "__main__":
    main()
