// Build site/jobs.json from the latest raw snapshot, using the app's OWN parser.
//
// The normalizer lives once, in site/index.html between `// <pure>` and `// </pure>`.
// We run that exact code here in a Node sandbox, so the hosted list and the app
// can never disagree about how a feed is read.
//
// Only sources marked redistributable (recorder/sources.py) are published.
// first-seen dates persist in data/first_seen.json.gz (powers "new since last visit").
//
// Usage: node pipeline/export.mjs [--data data] [--site site]
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import vm from "node:vm";

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const DATA = arg("--data", "data"), SITE = arg("--site", "site");
const DESC_MAX = 2500;           // enough for resume matching, keeps the bundle small
const FIRST_SEEN_KEEP_DAYS = 120;

// ---- load the app's pure logic
const html = fs.readFileSync(path.join(SITE, "index.html"), "utf8");
const m = html.match(/\/\/ <pure>([\s\S]*?)\/\/ <\/pure>/);
if (!m) throw new Error("index.html is missing the // <pure> ... // </pure> block");
const ctx = vm.createContext({ console });
vm.runInContext(m[1] + "\n;globalThis.__core = { normalizeText, isEmptyFeed: typeof isEmptyFeed === 'function' ? isEmptyFeed : null };", ctx);
const { normalizeText } = ctx.__core;

// ---- find the newest run
const idxFiles = fs.readdirSync(path.join(DATA, "index")).sort()
  .flatMap(d => fs.readdirSync(path.join(DATA, "index", d)).map(f => path.join(DATA, "index", d, f)))
  .filter(f => f.endsWith(".jsonl.gz")).sort((a, b) => path.basename(a).localeCompare(path.basename(b)));
if (!idxFiles.length) throw new Error("no snapshot index found");
const latest = idxFiles[idxFiles.length - 1];
const recs = zlib.gunzipSync(fs.readFileSync(latest)).toString("utf8").trim().split("\n").map(JSON.parse);
const runId = recs[0].run;
const rawDir = path.join(DATA, "raw", runId);
const safe = s => s.replace(/[^A-Za-z0-9\-_.]/g, "_");

// ---- previous first-seen map
const fsPath = path.join(DATA, "first_seen.json.gz");
let firstSeen = {};
try { firstSeen = JSON.parse(zlib.gunzipSync(fs.readFileSync(fsPath)).toString("utf8")); } catch (_) {}
const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");

// ---- normalize each redistributable source
const feeds = [], jobs = [];
let skippedTerms = 0;
for (const r of recs) {
  if (r.redistributable !== true) { skippedTerms++; continue; }
  const feed = { id: r.source, name: r.name, ok: false, count: 0, fetchedAt: r.at };
  feeds.push(feed);
  if (!r.ok) { feed.error = r.error || ("HTTP " + r.status); continue; }
  if (r.schema_error) { feed.error = "feed format changed: " + r.schema_error; continue; }
  const pages = fs.existsSync(rawDir)
    ? fs.readdirSync(rawDir).filter(f => f === safe(r.source) + ".body.gz" || (f.startsWith(safe(r.source) + ".p") && f.endsWith(".body.gz"))).sort()
    : [];
  if (!pages.length) { feed.error = "raw body missing"; continue; }
  try {
    for (const p of pages) {
      let text = zlib.gunzipSync(fs.readFileSync(path.join(rawDir, p))).toString("utf8");
      if (r.kind === "smartrecruiters") {           // its API omits a readable company name
        const d = JSON.parse(text); d.jfCompany = r.name; d.jfSlug = r.source.split(":")[1]; text = JSON.stringify(d);
      }
      let js = [];
      try { js = normalizeText(text); }
      catch (e) { if (r.job_count === 0) js = []; else throw e; }   // empty board is not an error
      for (const j of js) {
        if (j.description && j.description.length > DESC_MAX) j.description = j.description.slice(0, DESC_MAX);
        j.firstSeen = firstSeen[j.id] || (firstSeen[j.id] = now);
        jobs.push(j);
      }
      feed.count += js.length;
    }
    feed.ok = true;
  } catch (e) { feed.error = "could not read: " + String(e.message || e).slice(0, 120); }
}

// ---- prune first-seen entries for jobs long gone
const live = new Set(jobs.map(j => j.id));
const cutoff = Date.now() - FIRST_SEEN_KEEP_DAYS * 86400000;
for (const [k, v] of Object.entries(firstSeen)) if (!live.has(k) && Date.parse(v) < cutoff) delete firstSeen[k];

const okCount = feeds.filter(f => f.ok).length;
// Health gate below: a collapsed run exits non-zero so the deploy step is skipped
const prevPath = path.join(SITE, "jobs.json");
const bundle = { updatedAt: recs[0].at || now, run: runId, feeds, jobs,
                 note: skippedTerms + " sources are measured but not published (terms)" };
fs.mkdirSync(SITE, { recursive: true });
fs.writeFileSync(prevPath, JSON.stringify(bundle));
fs.writeFileSync(fsPath, zlib.gzipSync(JSON.stringify(firstSeen)));
const mb = (fs.statSync(prevPath).size / 1048576).toFixed(1);
console.log(JSON.stringify({ run: runId, feeds: feeds.length, ok: okCount, jobs: jobs.length, mb, skippedTerms }));
if (feeds.length && okCount / feeds.length < 0.5) { console.error("Over half the feeds failed; refusing to publish."); process.exit(3); }
