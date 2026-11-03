// Build site/onet_dict.json: the job-title dictionary site/relevance.js reads (in the page and in the pipeline).
// Output: { v, occ: {code: [title, jobZone, parent?, "eq"|"under"?]}, p: {curated keys}, t: {normalized title: [codes...]} }
// Usage: node pipeline/build_onet.mjs [data/onet] [site/onet_dict.json]
//
// Source: O*NET 30.1 Database (Occupation Data, Alternate Titles, Sample of Reported Titles, Job Zones) by the
// U.S. Department of Labor, Employment and Training Administration (USDOL/ETA), used under CC BY 4.0.
// Hire Ground has modified it: titles are normalized and a short list of modern titles is added below (EXTRA, CUSTOM).
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import "../site/relevance.js";
const { normTitle } = globalThis.HGRel;

const [dir = "data/onet", out = "site/onet_dict.json"] = process.argv.slice(2);
const read = f => zlib.gunzipSync(fs.readFileSync(path.join(dir, f + ".txt.gz"))).toString("utf8")
  .trim().split("\n").slice(1).map(l => l.split("\t"));

const occ = {};
for (const [code, title] of read("Occupation Data")) occ[code] = [title, 0];
for (const [code, zone] of read("Job Zones")) if (occ[code]) occ[code][1] = Number(zone);

const t = new Map(), pref = {};
const add = (title, code) => {
  const k = normTitle(title);
  if (!k) return;
  if (!t.has(k)) t.set(k, new Set());
  t.get(k).add(code);
};
for (const [code, title] of read("Occupation Data")) {
  add(title, code);                                          // "Registered Nurses" -> "registered nurse"
  // "Automotive Service Technicians and Mechanics" -> also "automotive service technician", "automotive service mechanic"
  const parts = title.replace(/,.*$/, "").split(/\s+and\s+/i);
  if (parts.length === 2 && !/\s/.test(parts[1].trim())) {
    const mods = parts[0].trim().split(/\s+/).slice(0, -1).join(" ");
    add(parts[0], code); add((mods + " " + parts[1]).trim(), code);
  }
}
for (const [code, title, short] of read("Alternate Titles")) { add(title, code); if (short && short !== "n/a") add(short, code); }
for (const [code, title] of read("Sample of Reported Titles")) add(title, code);

// Hand additions: modern titles O*NET is thin on. Kept short and generic; every entry maps to an existing O*NET code.
const EXTRA = {
  "15-1255.01": ["game designer", "systems designer", "combat designer", "level designer", "narrative designer", "live ops designer",
                  "liveops designer", "economy designer", "monetization designer", "gameplay designer", "technical designer",
                  "content designer game", "quest designer", "encounter designer", "mission designer", "game design intern", "level design intern",
                  "game design", "level design", "vp of design game"],
  "27-2012.00": ["game producer", "game director", "executive producer", "live ops producer", "development director", "producer", "game lead", "game manager",
                  "production intern", "associate producer", "production director", "head of production", "general manager live studio"],
  "15-1253.00": ["qa tester", "game tester", "qa analyst", "quality assurance analyst", "qa engineer", "quality assurance tester"],
  "11-2021.00": ["product marketing manager"],
  "15-1252.00": ["software engineer", "backend engineer", "frontend engineer", "full stack engineer", "gameplay engineer", "gameplay programmer",
                  "engine programmer", "graphics engineer", "mobile engineer", "ios engineer", "android engineer", "game programmer"],
  "15-1299.08": ["site reliability engineer", "platform engineer", "devops engineer", "infrastructure engineer", "cloud engineer"],
  "15-2051.00": ["data scientist", "machine learning engineer", "research scientist machine learning", "applied scientist"],
  "41-3091.00": ["account executive", "sales development representative", "business development representative", "account manager"],
  "13-1111.00": ["product operations"],
  "23-1011.00": ["counsel", "legal counsel", "corporate counsel", "product counsel", "commercial counsel", "general counsel", "privacy counsel",
                  "employment counsel", "regulatory counsel", "litigation counsel"],
  "41-2031.00": ["sales associate", "retail associate", "store associate", "keyholder"],
  "41-1011.00": ["store manager", "assistant store manager", "retail manager", "store director", "boutique manager"],
  "29-1171.00": ["nurse practitioner", "pmhnp", "fnp", "family nurse practitioner"],
  "29-1071.00": ["physician assistant"],
  "27-1014.00": ["level artist", "lighting artist", "vfx artist", "technical artist", "environment artist", "character artist", "3d artist", "concept artist", "animator"],
  "27-1024.00": ["ui artist", "graphic designer"],
  "15-1255.00": ["ux designer", "ui designer", "product designer", "ux ui designer", "interaction designer"],
  "11-3021.00": ["engineering manager", "director of engineering"],
  "13-1082.00": ["project manager", "program manager", "technical program manager"],
  "27-3031.00": ["community manager"],
  "13-2011.00": ["tax accountant", "senior accountant", "staff accountant", "revenue accountant", "accounting manager"]
};
for (const [code, list] of Object.entries(EXTRA)) for (const title of list) {
  if (!occ[code]) throw new Error("unknown code " + code);
  const k = normTitle(title); t.set(k, new Set([code, ...[...(t.get(k) || [])].filter(c => c !== code)])); pref[k] = 1;
}

// Occupations O*NET folds into a broader code but job seekers treat as their own field. Parent = closest O*NET code.
const CUSTOM = {
  "hg-pm": ["Product Managers", "11-2021.00", "under", ["product manager", "product owner", "group product manager", "product lead", "game product manager", "technical product manager", "director of product", "head of product", "product management intern", "product management", "publishing product manager"]],
  "hg-liveops": ["Live Operations (games)", "27-2012.00", "under", ["live ops manager", "liveops manager", "live operations manager", "live ops lead", "live ops director", "live operations director", "live game manager", "monetization manager", "game economy manager", "economy manager", "product economy manager"]],
  "hg-autotech": ["Automotive Service Technicians", "49-3023.00", "eq", ["automotive technician", "auto technician", "automotive service technician", "auto mechanic", "car mechanic", "lube technician", "service technician automotive", "ev technician", "automotive service advisor"]],
};
for (const [code, [title, parent, rel, list]] of Object.entries(CUSTOM)) {
  occ[code] = [title, occ[parent][1], parent, rel];   // rel "eq": same job as the parent; "under": a narrower field inside it
  for (const x of list) { const k = normTitle(x); t.set(k, new Set([code, ...[...(t.get(k) || [])].filter(c => c !== code)])); pref[k] = 1; }
}
const obj = { v: "onet-30.1", occ, p: pref, t: Object.fromEntries([...t].map(([k, s]) => [k, [...s]])) };
fs.writeFileSync(out, JSON.stringify(obj));
console.log({ occupations: Object.keys(occ).length, titles: t.size, kb: Math.round(fs.statSync(out).size / 1024) });
