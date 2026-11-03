// Build site/jobs.json from the latest raw snapshot, using the app's OWN parser.
//
// The normalizer lives once, in site/index.html between `// <pure>` and `// </pure>`.
// We run that exact code here in a Node sandbox, so the hosted list and the app
// can never disagree about how a feed is read.
//
// Only sources marked redistributable (recorder/sources.py) are published.
// first-seen dates persist in data/first_seen.json.gz (powers "new since last visit").
//
// With --cache DIR (production, see recorder/record.py): boards that answered "not modified" or were
// not due this run are read from the body cache, so an hourly games-only refresh still publishes
// every board.
//
// Usage: node pipeline/export.mjs [--data data] [--site site] [--cache cache]
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import vm from "node:vm";

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const DATA = arg("--data", "data"), SITE = arg("--site", "site"), CACHE = arg("--cache", null);
// 1,500 chars is plenty for resume matching. Non-games employer boards roughly triple the job count,
// so this keeps jobs.json light enough for phones.
const DESC_MAX = 1500;
// Employer boards are ~85% of jobs. Their fit verdict comes mostly from the title, so a shorter
// description costs little ranking and halves their weight. The full posting is one click away.
const DESC_MAX_EMPLOYER = 600;
// Posts open longer than this are mostly always-open roles that rarely lead anywhere: leave them out.
const MAX_AGE_DAYS = 180;
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

// Imported studio boards (recorder/import_openjobs.py): same key the app's studioInfo() builds.
const foldName = s => String(s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
const studioKeyOf = name => foldName(String(name).replace(/\s*\([^)]*\)$/, "")).replace(/[^a-z0-9]+/g, " ").trim();
// Game-adjacent imports (the dataset tags some non-game firms "gaming") only publish roles that are about games.
const NOT_GAMES = /\bgame[\s-]*(chang\w*|plan\w*|day)\b/gi;   // "game-changing", "game plan", "game day"
const isGameRole = j => GAME_ROLE.test(((j.title || "") + " " + (j.department || "") + " " + (j.description || "")).replace(NOT_GAMES, " "));
const GAME_ROLE = /\b(games?|gaming|gameplay|game ?dev\w*|unity|unreal|godot|level design\w*|esports?|live ?ops|player experience|playtest\w*)\b/i;

// ---- normalize each redistributable source
const feeds = [], jobs = [];
let skippedTerms = 0, droppedOld = 0, droppedOffTopic = 0;
const ageCutoff = Date.now() - MAX_AGE_DAYS * 86400000;
for (const r of recs) {
  if (r.redistributable !== true) { skippedTerms++; continue; }
  const feed = { id: r.source, name: r.name, ok: false, count: 0, fetchedAt: r.at };
  if (r.industry) feed.industry = r.industry;
  const employer = r.category === "employer";
  feeds.push(feed);
  if (!r.ok) { feed.error = r.error || ("HTTP " + r.status); continue; }
  if (r.schema_error) { feed.error = "feed format changed: " + r.schema_error; continue; }
  const pagesIn = dir => fs.existsSync(dir)
    ? fs.readdirSync(dir).filter(f => f === safe(r.source) + ".body.gz" || (f.startsWith(safe(r.source) + ".p") && f.endsWith(".body.gz"))).sort()
    : [];
  // Fresh 200 bodies are in this run's raw dir; 304 and carried boards live in the cache.
  let bodyDir = rawDir, pages = (r.carried || r.not_modified) ? [] : pagesIn(rawDir);
  if (!pages.length && CACHE) { bodyDir = CACHE; pages = pagesIn(CACHE); }
  if (!pages.length) { feed.error = "raw body missing"; continue; }
  if (r.carried) feed.checkedAt = r.at;
  try {
    for (const p of pages) {
      let text = zlib.gunzipSync(fs.readFileSync(path.join(bodyDir, p))).toString("utf8");
      if (r.kind === "smartrecruiters") {           // its API omits a readable company name
        const d = JSON.parse(text); d.jfCompany = r.name; d.jfSlug = r.source.split(":")[1]; text = JSON.stringify(d);
      }
      let js = [];
      try { js = normalizeText(text); }
      catch (e) { if (r.job_count === 0) js = []; else throw e; }   // empty board is not an error
      const descMax = employer ? DESC_MAX_EMPLOYER : DESC_MAX;
      for (const j of js) {
        if (j.posted && j.posted < ageCutoff) { droppedOld++; continue; }
        if (r.origin === "openjobs" && !j.gameStudio) {   // the app's own STUDIOS list wins when it knows the board
          if (r.adjacent && !isGameRole(j)) { droppedOffTopic++; j.offTopic = true; continue; }
          j.company = r.name; j.studioKey = studioKeyOf(r.name); j.gameStudio = !r.adjacent;
        }
        if (j.description && j.description.length > descMax) j.description = j.description.slice(0, descMax);
        j.firstSeen = firstSeen[j.id] || (firstSeen[j.id] = now);
        // Employer boards: the verified company name (Lever and Ashby feeds only carry the slug) + its industry.
        if (employer) { j.company = r.name; j.industry = r.industry; }
        jobs.push(j);
      }
      feed.count += js.filter(j => !(j.posted && j.posted < ageCutoff) && !j.offTopic).length;
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
// Newest check of any board (carried boards keep the time they were last checked).
const updatedAt = recs.filter(r => !r.carried).map(r => r.at).filter(Boolean).sort().pop() || recs[0].at || now;
const bundle = { updatedAt, run: runId, feeds, jobs,
                 note: skippedTerms + " sources are measured but not published (terms)" };
fs.mkdirSync(SITE, { recursive: true });
fs.writeFileSync(prevPath, JSON.stringify(bundle));
fs.writeFileSync(fsPath, zlib.gzipSync(JSON.stringify(firstSeen)));
const mb = (fs.statSync(prevPath).size / 1048576).toFixed(1);
console.log(JSON.stringify({ run: runId, feeds: feeds.length, ok: okCount, jobs: jobs.length, droppedOld, droppedOffTopic, mb, skippedTerms }));
if (Number(mb) > 15) console.warn("::warning::jobs.json is " + mb + " MB. Phones will feel it; consider a lower DESC_MAX_EMPLOYER or MAX_AGE_DAYS.");
if (feeds.length && okCount / feeds.length < 0.5) { console.error("Over half the feeds failed; refusing to publish."); process.exit(3); }
