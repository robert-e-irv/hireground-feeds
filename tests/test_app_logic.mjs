// Offline test for the app's own ranking logic (the // <pure> block in site/index.html).
// Run: node tests/test_app_logic.mjs
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const html = fs.readFileSync(new URL("../site/index.html", import.meta.url), "utf8");
const src = html.match(/\/\/ <pure>([\s\S]*?)\/\/ <\/pure>/)[1];
const ctx = vm.createContext({});
vm.runInContext(src + ";globalThis.__c = { rankJobs, titleFit, resumeVocab, industryMatch, FIT_LABEL, groupRoles, industryCounts };", ctx);
const { rankJobs, titleFit, resumeVocab, industryMatch, FIT_LABEL, groupRoles, industryCounts } = ctx.__c;

const NOW = Date.parse("2026-11-02T12:00:00Z"), day = 864e5;
const nurse = "Registered Nurse, BSN. Five years of acute care nursing on a medical surgical unit at a regional hospital, " +
  "plus emergency department triage. Patient assessment, medication administration, IV therapy, wound care, discharge planning " +
  "and patient education. Charge nurse on night shifts, precepting new graduate nurses. Infection control, care plans, " +
  "quality improvement on falls prevention and sepsis screening. BLS, ACLS and PALS certified. Telehealth nurse interest.";
const accountant = "Chartered Professional Accountant with six years in corporate accounting. Senior staff accountant for month-end close, " +
  "general ledger, account reconciliations, journal entries and accruals. Financial statements under IFRS, external audit, " +
  "accounts payable and accounts receivable. Variance analysis in Excel and NetSuite. Looking for senior accountant roles.";
const job = (id, title, desc, ageDays = 1) => ({ id, title, description: desc || "", posted: NOW - ageDays * day });

// 1. The old bug: a nurse's best match was a software job with a high-looking score. Now it is a Long shot.
const games = [job("a", "Software Engineer, Creator Services Communication"), job("b", "Senior Manager, Regional Security"),
  job("c", "Senior Quality Engineer"), job("d", "Brand Planning Manager")];
const r1 = rankJobs(nurse, games, { recencyWeight: 0.3, now: NOW });
assert.ok(r1.length > 0, "still lists the closest jobs");
assert.ok(r1.every(j => j.tier === 0), "no games role is called a fit for a nurse: " + r1.map(j => j.title + "=" + j.tier).join("; "));

// 2. A real nursing job ranks first and is a Strong fit, even when it is older.
const r2 = rankJobs(nurse, games.concat(job("n", "Registered Nurse, Telehealth", "RN for virtual care.", 20)), { recencyWeight: 0.3, now: NOW });
assert.equal(r2[0].id, "n");
assert.equal(FIT_LABEL[r2[0].tier], "Strong fit");

// 3. Accountant: an accountant role is Strong, an unrelated role is not.
const v = resumeVocab(accountant);
assert.ok(titleFit("Senior Accountant, Royalties", v).fit >= 0.5);
assert.ok(titleFit("Lead Game Designer", v).fit < 0.25);

// 4. Titles that clean down to a generic word still get judged on their full words.
assert.ok(titleFit("Intern - Senior Accountant", v).fit >= 0.5);

// 5. Employer boards carry their industry: a clinic's front-desk role counts as Healthcare.
assert.ok(industryMatch({ title: "Front Desk Coordinator", company: "Good Clinic", industry: "health" }, "health"));
assert.ok(!industryMatch({ title: "Front Desk Coordinator", company: "Good Clinic", industry: "health" }, "finance"));

// 6. One card per role: same company + title in several cities collapses; the first (best-ranked) copy leads.
const g = groupRoles([
  { id: "1", company: "Stripe", title: "Senior Accountant", location: "Toronto" },
  { id: "2", company: "Acme", title: "Senior Accountant", location: "Toronto" },
  { id: "3", company: "Stripe", title: "Sr. Accountant", location: "Dublin" },
  { id: "4", company: "Stripe", title: "Senior Accountant, Tax", location: "Seattle" }]);
assert.deepEqual(Array.from(g, j => j.id), ["1", "2", "4"], "different companies and different roles stay separate");
assert.deepEqual(Array.from(g[0].others, j => j.location), ["Dublin"], "Sr. and Senior are the same role");

// 7. Industry counts come from the board's industry, plus title keywords for everyone else.
const c = industryCounts([{ title: "Staff Accountant", company: "X", industry: "health" }, { title: "Registered Nurse", company: "Y" }, { title: "Game Designer", company: "Z", gameStudio: true }]);
assert.equal(c.health, 2); assert.equal(c.finance, 1); assert.equal(c.games, 1); assert.equal(c.education, 0);

console.log("ALL PASSED");
