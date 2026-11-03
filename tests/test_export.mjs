// Offline test for pipeline/export.mjs: description caps, the 180-day cut, employer name + industry.
// Run: node tests/test_export.mjs
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hg-export-"));
const data = path.join(tmp, "data"), site = path.join(tmp, "site"), run = "20261102T000000Z";
fs.mkdirSync(path.join(data, "index", "2026-11-02"), { recursive: true });
fs.mkdirSync(path.join(data, "raw", run), { recursive: true });
fs.mkdirSync(site, { recursive: true });
fs.copyFileSync(path.join(ROOT, "site", "index.html"), path.join(site, "index.html"));

const day = 864e5, iso = ms => new Date(ms).toISOString();
const long = "x".repeat(3000);
const gh = (slug, jobs) => ({ jobs: jobs.map(([id, title, ageDays]) => ({ id, title, content: long,
  absolute_url: `https://job-boards.greenhouse.io/${slug}/jobs/${id}`, first_published: iso(Date.now() - ageDays * day) })) });
const boards = {
  "greenhouse:goodclinic": { name: "Good Clinic", category: "employer", industry: "health",
    body: gh("goodclinic", [[1, "Registered Nurse", 5], [2, "Charge Nurse", 400]]) },
  "greenhouse:riotgames": { name: "Riot Games", category: "studio",
    body: gh("riotgames", [[3, "Game Designer", 10]]) },
  // Imported from OpenJobs: a real studio, and a "gaming"-tagged firm that only publishes game roles.
  "greenhouse:moonpixel": { name: "Moon Pixel Studio", category: "studio", origin: "openjobs", adjacent: false,
    body: gh("moonpixel", [[4, "Payroll Specialist", 3]]) },
  "greenhouse:acmeco": { name: "Acme Co", category: "studio", origin: "openjobs", adjacent: true,
    body: { jobs: [
      { id: 5, title: "Unity Developer", content: "Build our game client", absolute_url: "a", first_published: iso(Date.now() - day) },
      { id: 6, title: "Hotel Night Manager", content: "A game-changing guest experience", absolute_url: "b", first_published: iso(Date.now() - day) }] } },
};
const lines = Object.entries(boards).map(([id, b]) => {
  fs.writeFileSync(path.join(data, "raw", run, id.replace(/[^A-Za-z0-9\-_.]/g, "_") + ".body.gz"), zlib.gzipSync(JSON.stringify(b.body)));
  return JSON.stringify({ run, source: id, name: b.name, kind: "greenhouse", category: b.category, industry: b.industry,
    origin: b.origin, adjacent: b.adjacent,
    redistributable: true, at: iso(Date.now()), ok: true, job_count: b.body.jobs.length });
});
fs.writeFileSync(path.join(data, "index", "2026-11-02", run + ".jsonl.gz"), zlib.gzipSync(lines.join("\n") + "\n"));

const log = execFileSync("node", [path.join(ROOT, "pipeline", "export.mjs"), "--data", data, "--site", site], { encoding: "utf8" });
const out = JSON.parse(fs.readFileSync(path.join(site, "jobs.json"), "utf8"));
const byTitle = Object.fromEntries(out.jobs.map(j => [j.title, j]));

assert.ok(!byTitle["Charge Nurse"], "a post older than 180 days is left out");
assert.equal(JSON.parse(log.trim().split("\n")[0]).droppedOld, 1);
const rn = byTitle["Registered Nurse"];
assert.ok(rn.description.length <= 600, "employer descriptions are capped at 600 chars");
assert.equal(rn.company, "Good Clinic");
assert.equal(rn.industry, "health");
assert.ok(byTitle["Game Designer"].description.length > 600 && byTitle["Game Designer"].description.length <= 1500, "studio jobs keep 1,500 chars");
assert.equal(out.feeds.find(f => f.id === "greenhouse:goodclinic").count, 1, "feed count matches what is published");
const pay = byTitle["Payroll Specialist"];
assert.equal(pay.company, "Moon Pixel Studio", "imported studio jobs get the company's name");
assert.equal(pay.gameStudio, true, "every role at an imported game studio counts as games");
assert.equal(pay.studioKey, "moon pixel studio");
assert.equal(byTitle["Unity Developer"].company, "Acme Co");
assert.equal(byTitle["Unity Developer"].gameStudio, false, "adjacent: only game roles, not a studio");
assert.ok(!byTitle["Hotel Night Manager"], "adjacent import drops roles that aren't about games");
assert.equal(out.feeds.find(f => f.id === "greenhouse:acmeco").count, 1);
assert.equal(JSON.parse(log.trim().split("\n")[0]).droppedOffTopic, 1);
assert.equal(byTitle["Game Designer"].gameStudio, true, "the app's own studio list still applies");
fs.rmSync(tmp, { recursive: true, force: true });
console.log("ALL PASSED");
