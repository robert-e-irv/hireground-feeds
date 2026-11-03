// Offline test for site/relevance.js: occupation, level and licence reading, and who sees which job.
// Each case is a mistake the old keyword matcher made, or one found while building the new one.
// Run: node tests/test_relevance.mjs
import fs from "node:fs";
import assert from "node:assert/strict";
import "../site/relevance.js";
const R = globalThis.HGRel;
const dict = JSON.parse(fs.readFileSync(new URL("../site/onet_dict.json", import.meta.url), "utf8"));
const occ = t => { const m = R.matchTitle(t, dict); return m && m.code; };
const NOW = Date.parse("2026-11-03T12:00:00Z"), day = 864e5;

// 1. Titles -> occupations, for many fields (not just games).
assert.equal(occ("Registered Nurse - Telemetry"), "29-1141.00");
assert.equal(occ("Senior Tax Accountant, EMEA"), "13-2011.00");
assert.equal(occ("Senior Legal Counsel"), "23-1011.00");
assert.equal(occ("Automotive Service Technician"), "hg-autotech");
assert.equal(occ("Mechanical Engineer, Hardware Systems"), "17-2141.00", "a mechanical engineer is not a mechanic");
assert.equal(occ("Senior Product Manager - Games"), "hg-pm");
assert.equal(occ("Assistant Store Manager"), "41-1011.00");
assert.equal(occ("Marketing Lead"), "11-2021.00", "'X Lead' reads as 'X Manager'");
assert.equal(occ("Manager, Payments and Fraud Analytics").slice(0, 2), "15", "'Manager, X' reads as 'X manager'");
assert.equal(occ("1099 Supervising Physician - APP Supervision") === "29-1141.02", false, "a 3-letter acronym never decides the field");
assert.equal(occ("Senior Director, Global Total Rewards"), "11-3111.00", "'Director' alone is not Producers and Directors");

// 2. Levels on one ladder for every field.
const L = t => R.LEVEL_NAMES[R.levelOf(t)];
assert.equal(L("Physician Assistant"), "Mid", "'Assistant' as the job noun is not a level");
assert.equal(L("Sales Associate"), "Mid");
assert.equal(L("Associate Game Designer"), "Entry");
assert.equal(L("Staff Accountant"), "Mid", "'Staff' is senior only in engineering-type titles");
assert.equal(L("Staff Software Engineer"), "Lead");
assert.equal(L("Product Manager Intern (2027) - Austin, TX"), "Entry");
assert.equal(L("Senior Director, Corporate Counsel"), "Director");

// 3. Game studio context: design titles are game design there.
const tech = R.enrichJob({ title: "Technical Designer", gameStudio: true, description: "" }, dict);
assert.equal(tech.oc[0], "15-1255.01");
const voice = R.enrichJob({ title: "Content Creator & Strategist", description: "voice AI" }, dict);
assert.notEqual(voice.oc[0], "15-1255.01", "'Video Game Designer' only exists inside games");

// 4. A resume becomes a profile.
const gameResume = `Pat Doe\nVictoria, BC\nExperience\nACME GAMES, Victoria - Lead Game Designer\nMay 2021 - PRESENT\n- Live ops for an MMO game; players, events\n` +
  `ACME GAMES, Victoria - Senior Quality Analyst\nFeb 2019 - May 2021\n- Test plans\nSkills\n- Unity, Unreal, game design`;
const gp = R.profileFromResume(gameResume, dict, 2026);
assert.deepEqual(gp.targets, ["15-1255.01"]);
assert.equal(R.LEVEL_NAMES[gp.level], "Lead", "the level comes from the title part, not the company");
assert.deepEqual(gp.past, ["15-1253.00"], "QA is offered as a past field, never searched by default");
assert.equal(gp.domain, "games");
const rnResume = `Jo Lee, RN\nExperience\nRegistered Nurse - Med-Surg, City Hospital (2019 - Present)\n- 32-bed unit\nLicenses\nRegistered Nurse (RN), Pennsylvania`;
const rp = R.profileFromResume(rnResume, dict, 2026);
assert.deepEqual(rp.targets, ["29-1141.00"]);
assert.deepEqual(rp.creds, ["rn"], "a '32-bed' unit is not a B.Ed teaching degree");

// 5. Gates: field, level, licence, and remote limits.
const job = (id, title, extra = {}) => R.enrichJob(Object.assign({ id, title, description: "", posted: NOW - 5 * day, company: "Co" }, extra), dict);
const run = (p, jobs, country) => R.gateAndRank(jobs, p, { dict, now: NOW, userCountry: country, evalOf: j => ({ arrangement: j.rm || "onsite", fit: "" }) });
const nurse = run(rp, [job("a", "Registered Nurse - ICU"), job("b", "Nurse Practitioner - Virtual Care", { description: "Active NP license required." }),
  job("c", "Software Engineer"), job("d", "Charge Nurse, Emergency"),
  job("e", "Registered Nurse - Federal Clinic", { description: "An active Secret clearance is required." })]);
assert.deepEqual(nurse.list.map(j => j.id).sort(), ["a", "d"], "a 7-year RN sees staff and charge RN roles: " + JSON.stringify(nurse.hidden));
assert.equal(nurse.hidden.field, 2, "Nurse Practitioner is its own field; software is not nursing");
assert.equal(nurse.hidden.cred, 1, "a role needing a clearance the person doesn't list is hidden");
const gd = run(gp, [job("1", "Senior Game Designer", { gameStudio: true }), job("2", "Associate Game Designer", { gameStudio: true }),
  job("3", "Senior UX Designer"), job("4", "QA Tester", { gameStudio: true }), job("5", "Game Director", { gameStudio: true }),
  job("6", "Senior Game Designer", { gameStudio: true, rm: "remote", description: "You must reside in the United States." }),
  job("8", "Design Director", { gameStudio: true })], "Canada");
assert.deepEqual(gd.list.map(j => j.id), ["1"], "only the senior game design role in reach: " + JSON.stringify(gd.hidden));
assert.equal(gd.hidden.junior, 1, "Associate is below a lead's range");
assert.equal(gd.hidden.field, 3, "UX design, QA and game direction (production) are other fields");
assert.equal(gd.hidden.senior, 1, "Design Director is above a lead's default range");
assert.equal(gd.hidden.where, 1, "a US-only remote role is hidden for someone in Canada");

// 6. The person's corrections win: add a field, widen the level, games only.
const ep = R.applyEdits(gp, { add: ["27-2012.00"], levelMax: 6, onlyDomain: true });
const gd2 = run(ep, [job("5", "Game Director", { gameStudio: true }), job("7", "Producer", { industry: "software" })]);
assert.deepEqual(gd2.list.map(j => j.id), ["5"]);
assert.equal(gd2.hidden.domain, 1, "a producer outside games is hidden when the person asks for games only");

// 7. Freshness orders, it doesn't gate; a repost keeps its original age.
const p3 = run(rp, [job("old", "Registered Nurse", { posted: NOW - 90 * day }), job("new", "Registered Nurse - Days", { posted: NOW - 2 * day })]);
assert.deepEqual(p3.list.map(j => j.id), ["new", "old"]);
console.log("ALL PASSED");
