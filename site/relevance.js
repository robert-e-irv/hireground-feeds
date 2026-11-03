// Hire Ground relevance: what a job IS (occupation, level, licence), what a person IS (from their resume),
// and which jobs to show them. One file, used three ways so they can never disagree:
//   the page (site/index.html loads it), the pipeline (pipeline/export.mjs runs it per posting), and the tests.
// Occupations come from O*NET 30.1 (site/onet_dict.json; see pipeline/build_onet.mjs).
(function (root) {
"use strict";

const fold = s => String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
const SING = w => w.length > 4 && /[^s]s$/.test(w) && !/(ss|us|is|ics|ops|sales|series|news)$/.test(w) ? w.replace(/ies$/, "y").replace(/s$/, "") : w;

// Words that say how senior a job is, not what it is.
// Only pure seniority adjectives: "manager", "director" and "assistant" are part of what a job IS ("product manager").
const LEVEL_WORDS = /\b(senior|sr|snr|junior|jr|lead|principal|staff|intern|internship|trainee|graduate|entry level|entry|mid level|mid|experienced|expert|distinguished|i{1,3}|iv|v|[1-5])\b/g;
const NOISE = /\b(remote|hybrid|onsite|on site|in office|contract|contractor|freelance|temporary|temp|part time|full time|fixed term|permanent|per diem|prn|bilingual|f m d|m f d|m w d|w m d|all genders|x f m)\b/g;

const ROLE_ONLY = /^(manager|director|vp|vice president|head|head of|lead|team lead|associate|specialist|consultant|supervisor|partner|officer|executive|senior manager|associate director|managing director|svp|evp)$/;
// A single word that names many occupations ("manager", "engineer") never decides the field on its own.
const GENERIC_ONE = new Set("manager director lead head vp president partner associate specialist coordinator consultant analyst assistant administrator representative agent executive officer owner operator technician engineer designer artist advisor adviser strategist scientist researcher generalist expert developer programmer supervisor worker clerk inspector instructor teacher trainer".split(" "));
// Synonyms for the role noun, tried when the title as written is not an O*NET title: "Marketing Lead" -> "marketing manager".
const ROLE_SWAP = [[/\b(lead|head|director|vp|owner)$/, "manager"], [/\bengineer$/, "developer"], [/\bexec$/, "executive"], [/\bspecialist$/, "analyst"]];
// Title text -> lookup key: folded, punctuation gone, plurals singular. Level words kept (some O*NET titles contain them).
function normTitle(s) {
  return fold(s).replace(/&/g, " and ").replace(/[^a-z0-9+#]+/g, " ").trim().split(/\s+/).filter(Boolean).map(SING).join(" ");
}
function stripLevel(k) { return k.replace(LEVEL_WORDS, " ").replace(/\s+/g, " ").trim(); }

// Split "Senior Tax Accountant, EMEA - Remote | Team X" into candidate segments, most important first.
function segments(title) {
  let t = String(title || "");
  const paren = [...t.matchAll(/\(([^)]*)\)/g)].map(m => m[1]);
  t = t.replace(/\([^)]*\)|\[[^\]]*\]/g, " ");
  const segs = t.split(/\s[-–—|:]\s|\s*[,/|;]\s*|\s+at\s+/i).map(s => s.trim()).filter(Boolean);
  const out = [];
  for (let i = 0; i < segs.length; i++) {
    const bare = stripLevel(normTitle(segs[i]));
    if (ROLE_ONLY.test(bare) && segs[i + 1]) { out.push(segs[i + 1] + " " + bare); out.push(segs[i] + " " + segs[i + 1]); i++; }
    else out.push(segs[i].replace(/^head of\s+(.*)$/i, "$1 manager"));
  }
  return out.concat(paren);
}

// Best occupation(s) for a title. Returns { code, conf, phrase, alts } or null.
// conf: 1 = an exact O*NET title (after removing level words), 0.8 = a phrase inside the title, 0.5 = token overlap.
function matchTitle(title, dict) {
  const T = dict.t;
  let best = null;
  const consider = (codes, conf, phrase, segIdx) => {
    // Prefer earlier segments and longer phrases; a phrase naming many occupations is weak (unless it's a curated reading).
    const spec = dict.p && dict.p[phrase] ? 1 : 1 / Math.sqrt(codes.length);
    const s = conf * spec * (1 - 0.15 * segIdx) + phrase.split(" ").length * 0.01;
    if (!best || s > best.s) best = { s, code: codes[0], alts: codes.slice(1, 4), conf: +(conf * spec).toFixed(2), phrase };
  };
  segments(title).forEach((seg, i) => {
    if (i > 3) return;
    const full = normTitle(seg).replace(NOISE, " ").replace(/\s+/g, " ").trim();
    const bare = stripLevel(full);
    for (const k of [full, bare]) if (k && T[k] && (k.length > 3 || (dict.p && dict.p[k]))) consider(T[k], 1, k, i);
    for (const base of [full, bare]) for (const [re, rep] of ROLE_SWAP) { const k = base.replace(re, rep); if (k !== base && T[k]) consider(T[k], 0.8, k, i); }
    // Phrases inside the title. English job titles put the noun last ("tax accountant", "game designer").
    const w = bare.split(" ").filter(Boolean);
    for (let len = Math.min(w.length, 5); len >= 1; len--) {
      for (let a = 0; a + len <= w.length; a++) {
        const k = w.slice(a, a + len).join(" ");
        if (!T[k]) continue;
        if (len === 1 && (T[k].length > 3 || GENERIC_ONE.has(k) || (k.length <= 3 && !(dict.p && dict.p[k])))) continue;   // "app", "pa": acronyms too risky   // "designer", "manager": too vague alone
        const endsAtHead = a + len === w.length;
        consider(T[k], (endsAtHead ? 0.85 : 0.6) * (len / w.length) ** 0.25, k, i);
      }
    }
  });
  if (!best) return null;
  return { code: best.code, alts: best.alts, conf: best.conf, phrase: best.phrase };
}

// ---- level: one ladder for every field
// 1 entry, 2 mid, 3 senior, 4 lead (lead, principal, staff, supervisor, charge, foreman), 5 manager / head, 6 director / VP, 7 executive
const LEVEL_NAMES = ["", "Entry", "Mid", "Senior", "Lead", "Manager", "Director", "Executive"];
const LEVEL_RULES = [
  [7, /\b(chief|ceo|cfo|coo|cto|cmo|cpo|president|founder|general counsel)\b/],
  [6, /\b(vp|vice president|svp|evp|director|head of (?!.*\b(team|shift)\b)|executive producer|game director|creative director|design director|managing director|deputy general counsel|partner)\b/],
  [5, /\b(manager|mgr|head|store manager)\b/],
  [4, /\b(lead|principal|staff (?=\w*\s*(engineer|scientist|designer|researcher|developer|architect|analyst|product|data|software|technical|game))|supervisor|superintendent|charge|foreman|forewoman|team lead|architect|distinguished)\b/],
  [3, /\b(senior|sr|snr|iii|iv|experienced|journeyman|journeyperson|master)\b/],
  // "Assistant"/"associate" are a level only BEFORE the job noun ("Assistant Editor"), not as the noun ("Physician Assistant").
  [1, /\b(intern|internship|trainee|apprentice|graduate|new grad|entry level|junior|jr|i)\b|\b(associate|assistant|asst)\b(?= \w)(?! (director|general counsel|partner|producer|to\b))/],
];
// "Assistant Store Manager" is a manager-track role a step below the manager.
function levelOf(title) {
  const k = normTitle(String(title || "").replace(/\([^)]*\)/g, " ").split(/\s[-–|]\s|,/)[0]);
  if (/\b(intern|internship|trainee|apprentice|new grad|entry level|co op|working student|werkstudent)\b/.test(k)) return 1;
  if (/\bassistant (store )?manager\b/.test(k)) return 4;
  if (/\bassociate (director)\b/.test(k)) return 5;
  if (/\b(senior|sr) (manager|mgr)\b/.test(k)) return 5;
  if (/\b(senior|sr) director\b/.test(k)) return 6;
  for (const [lv, re] of LEVEL_RULES) if (re.test(k)) return lv;
  return 2;
}
// Years of experience a posting asks for ("5+ years", "minimum of 3 years").
function yearsRequired(text) {
  const t = fold(text).slice(0, 6000);
  let min = null;
  for (const m of t.matchAll(/\b(\d{1,2})\s*(?:\+|plus)?\s*(?:-|to|–)?\s*(?:\d{1,2})?\s*\+?\s*years?\b(?![^.]{0,40}\b(?:old|of age)\b)/g)) {
    const n = Number(m[1]);
    if (n >= 1 && n <= 25 && (min === null || n < min)) min = n;   // the smallest stated minimum is the gate
  }
  return min;
}

// ---- credentials a posting requires, and that a resume shows
// Each: id, label, what in a POSTING means it's required, what in a RESUME means the person has it.
const CREDENTIALS = [
  { id: "rn", label: "Registered Nurse licence",
    need: /\b(active|current|unrestricted|valid)\s+(?:\w+\s+){0,3}(rn|registered nurse)\s+licen[sc]e|\brn licen[sc]e (is )?required|\bmust be (a )?(licensed )?registered nurse/,
    have: /\b(rn|registered nurse)\b|\bbsn\b/ },
  { id: "np", label: "Nurse Practitioner licence",
    need: /\bnurse practitioner\b|\b(fnp|pmhnp|agnp|aprn|np)[- ]?(c|bc)?\b(?=[^.]{0,60}(licen|certif|board))|\bprescriptive authority\b/,
    have: /\b(nurse practitioner|fnp|pmhnp|aprn|msn[, ]+np|np-c)\b/ },
  { id: "md", label: "Physician licence (MD/DO)",
    need: /\b(board[- ]certified|board[- ]eligible) (physician|psychiatrist)|\b(md|do) (degree )?(and|with) (an )?(active|unrestricted)|\bphysician licen[sc]e\b/,
    have: /\b(m\.?d\.?|d\.?o\.?)\b(?=[, ]|$)|\bresidency\b/ },
  { id: "pa", label: "Physician Assistant licence",
    need: /\bphysician assistant\b(?=[^.]{0,80}(licen|certif|nccpa))|\bpa-c\b/, have: /\b(pa-c|physician assistant)\b/ },
  { id: "bar", label: "Bar admission / law licence",
    need: /\b(admitted to (practice|the bar)|bar admission|member (in good standing )?of (the|a|at least one) (state )?bar|licensed (to practice law|attorney)|active bar|j\.?d\.? (degree )?(and|with) (bar|admission)|law society)\b/,
    have: /\b(j\.?d\.?|juris doctor|ll\.?b|bar admission|called to the bar|law society|admitted to (the )?bar|esq)\b/ },
  { id: "cpa", label: "CPA",
    need: /\bcpa (licen[sc]e|certification|designation)? ?(is )?(required|must)|\b(must|required to) (have|hold|be) (an? )?(active )?(cpa|chartered accountant|cpa,? ca)\b|\bactive cpa\b/,
    have: /\b(cpa|chartered (professional )?accountant|\bca\b|acca|cma)\b/ },
  { id: "trade", label: "Trade certification (Red Seal / journeyperson)",
    need: /\b(red seal|journeyman|journeyperson|ase certif\w*|master technician|licensed (electrician|plumber|mechanic))\b/,
    have: /\b(red seal|journeyman|journeyperson|ase certif\w*|master technician|apprenticeship)\b/ },
  { id: "pe", label: "Professional Engineer (PE/P.Eng)",
    need: /\b(p\.?e\.? licen[sc]e|professional engineer(ing)? licen[sc]e|p\.?eng\b|eit\b)/, have: /\b(p\.?\s?eng|professional engineer|\bpe\b|eit)\b/ },
  { id: "teach", label: "Teaching certificate",
    need: /\b(teaching (licen[sc]e|certificat\w+|credential)|certified teacher|state teaching)\b/, have: /\b(teaching (licen[sc]e|certificat\w+|credential)|b\.ed\b|bachelor of education|certified teacher)\b/ },
  { id: "clearance", label: "Security clearance",
    need: /\b(active|current) (secret|top secret|ts\/sci|ts sci|security) clearance|\bclearance (is )?required\b|\bmust (hold|have|possess) (an? )?(active )?(secret|top secret|ts)/,
    have: /\b(secret|top secret|ts\/sci) clearance\b/ },
];
function credentialsRequired(text) {
  const t = fold(text).slice(0, 8000);
  return CREDENTIALS.filter(c => c.need.test(t)).map(c => c.id);
}
function credentialsHeld(text) {
  const t = fold(text);
  return CREDENTIALS.filter(c => c.have.test(t)).map(c => c.id);
}

// ---------- posting side: runs once per posting in the pipeline (pipeline/export.mjs) and is shipped in jobs.json.
// Short keys keep jobs.json small: oc occupation codes, ocf match confidence, lv level, yr years asked for,
// cr credentials required, el countries a remote job is limited to, dom "games" or the board's industry.
const DOMAIN_BOUND = { "15-1255.01": "games", "hg-liveops": "games" };
const GAME_RE = /\b(games?|gaming|gameplay|game ?dev\w*|live ?ops|esports?|unity|unreal|playtest\w*)\b/i;
const NOT_GAME_DESIGN = /\b(ux|ui|graphic|product|visual|brand|interior|fashion|motion|web|marketing|instructional|industrial|hardware|mechanical|electrical|circuit|pcb|asic|chip|cad|landscape|floral|jewelry)\b/;
function enrichJob(j, dict) {
  let m = matchTitle(j.title, dict);
  const isGame = !!j.gameStudio || GAME_RE.test((j.title || "") + " " + (j.dept || "")) ||
    (String(j.description || "").slice(0, 3000).match(/\b(games?|gaming|gameplay|players?)\b/gi) || []).length >= 4;
  // Inside games, design and production titles mean game design and game production, whatever O*NET would guess
  // ("Technical Designer" at a studio is not a drafter; "Senior Match-3 Designer" is a game designer).
  if (isGame) {
    const t = normTitle(j.title);
    if (/\bdesign(er)?\b/.test(t) && !NOT_GAME_DESIGN.test(t) && !/\b(audio|sound|presentation|asset|investment)\b/.test(t) && (!m || m.conf < 0.9 || /designer$/.test(m.phrase)))
      m = { code: "15-1255.01", alts: [], conf: 0.9, phrase: "game design" };
    else if (/\b(produc(er|tion)|game (lead|manager|director)|development director|release manager|(project|program) manager)\b/.test(t) && (!m || m.conf < 0.9 || /^(15|13)-/.test(m.code)))
      m = { code: "27-2012.00", alts: [], conf: 0.9, phrase: "game production" };
  }
  // Some fields only exist inside one industry: a "content creator" at a voice-AI company is not a game designer.
  if (m && DOMAIN_BOUND[m.code] && !isGame) m = m.alts.length ? { code: m.alts[0], alts: m.alts.slice(1), conf: m.conf * 0.7, phrase: m.phrase } : null;
  const text = (j.title || "") + "\n" + (j.description || "");
  j.oc = m ? [m.code].concat(m.alts) : [];
  j.ocf = m ? m.conf : 0;
  j.lv = levelOf(j.title);
  j.yr = yearsRequired(j.description || "");
  j.cr = credentialsRequired(text);
  j.el = remoteEligibility(text);
  j.dom = isGame ? "games" : (j.industry || "");
  return j;
}
// Countries a remote job says you must live in ("must reside in the US", "Canada only").
const ELIG = [
  ["United States", /\b(must|required to|need to)\s+(be\s+)?(live|reside|be located|be based|located|based)\s+(with)?in\s+(the\s+)?(us|usa|u\.s\.?|united states|contiguous|continental)\b|\b(us|usa|u\.s\.)[- ](only|based only)\b|\b(only|open to)\s+(candidates|applicants|residents)\s+(in|of|based in|located in)\s+(the\s+)?(us|usa|united states)\b|\bauthori[sz]ed to work in the (us|united states)\b(?![^.]{0,40}canada)/],
  ["Canada", /\b(must|required to)\s+(be\s+)?(live|reside|be located|be based|located|based)\s+in\s+canada\b|\bcanada[- ]only\b|\bauthori[sz]ed to work in canada\b(?![^.]{0,40}(us|united states))/],
  ["United Kingdom", /\b(must|required to)\s+(be\s+)?(live|reside|be located|be based|located|based)\s+in\s+the\s+(uk|united kingdom)\b|\buk[- ]only\b|\bright to work in the uk\b/],
];
function remoteEligibility(text) {
  const t = fold(text).slice(0, 10000);
  return ELIG.filter(r => r[1].test(t)).map(r => r[0]);
}

// ---------- person side: runs in the browser. The resume never leaves it.
const MONTH = "(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\\.?\\s+";
const RANGE = new RegExp("(?:" + MONTH + ")?((?:19|20)\\d{2})\\s*(?:-|–|—|to)\\s*(?:(?:" + MONTH + ")?((?:19|20)\\d{2})|(present|current|now|today))", "i");
const SECTION_END = /^(education|skills|certifications?|licen[sc]es|bar admissions?|references|interests|projects)\b/i;
const GAME_WORDS = /\b(games?|gaming|gameplay|live[- ]?ops|players?|mmo\w*|unity|unreal|godot)\b/gi;
function profileFromResume(text, dict, nowYear) {
  const lines = String(text || "").split(/\r?\n/).map(s => s.trim());
  const roles = [], expYears = [];
  let section = "head";
  for (let i = 0; i < lines.length; i++) {
    const L = lines[i];
    if (!L) continue;
    if (/^(experience|work experience|employment|professional experience|work history|career history)\b/i.test(L)) { section = "exp"; continue; }
    if (SECTION_END.test(L)) { section = "other"; continue; }
    const anyR = section === "exp" && RANGE.exec(L);
    if (anyR) expYears.push(Number(anyR[1]));                // every dated job counts toward total years, read or not
    // No headings at all (pasted text, some PDFs): read every short line as a possible job line.
    if ((section !== "exp" && section !== "head") || /^[-•●*▪◦]/.test(L) || L.length > 140) continue;
    const m = matchTitle(L.replace(RANGE, " "), dict);
    if (!m || m.conf < 0.4) continue;
    const r = RANGE.exec(L) || RANGE.exec(lines[i + 1] || "");
    if (section === "head" && !r) continue;               // before "Experience", only lines with dates count as jobs
    const start = r ? Number(r[1]) : null, end = r ? (r[3] ? nowYear : Number(r[2])) : null;
    roles.push({ line: L, code: m.code, codes: [m.code].concat(m.alts.slice(0, m.conf < 1 ? 2 : 0)), phrase: m.phrase,
                 level: levelOf(titlePart(L, m.phrase)), start, end, current: !!(r && r[3]) });
  }
  const current = roles.find(r => r.current) || roles[0] || null;
  const starts = roles.filter(r => r.start).map(r => r.start).concat(expYears);
  const yearsIn = codes => roles.filter(r => r.codes.some(c => codes.indexOf(c) >= 0) && r.start)
    .reduce((s, r) => s + Math.max(0.5, (r.end || nowYear) - r.start), 0);
  const totalYears = starts.length ? nowYear - Math.min.apply(null, starts) : 0;
  // No level word in the title ("Registered Nurse"): years are the better evidence.
  let level = current ? current.level : 2;
  if (level === 2 && !(current && /\b(mid|ii|intermediate)\b/i.test(current.line))) level = totalYears >= 10 ? 4 : totalYears >= 5 ? 3 : totalYears >= 2 ? 2 : totalYears ? 1 : 2;
  const targets = current ? [current.code] : [];
  // Earlier jobs in a different field: offered as one-tap additions, never added on their own (most career changers left them on purpose).
  const sameAsTarget = c => targets.some(t => eqOf(t, dict) === eqOf(c, dict));
  const past = uniq(roles.filter(r => r !== current).map(r => r.code)).filter(c => !sameAsTarget(c));
  const suggest = [];
  return {
    targets, past, suggest, phrases: current ? [current.phrase] : [],
    domain: (String(text).match(GAME_WORDS) || []).length >= 4 ? "games" : "",
    // Default range: one step down, two up (Senior Accountant -> Accounting Manager is a normal next move).
    level, levelMin: Math.max(1, level - 1), levelMax: Math.min(7, level + 2),
    years: current ? yearsIn(current.codes) : 0, totalYears,
    creds: credentialsHeld(text), roles, words: String(text || ""),
  };
}
const uniq = a => Array.from(new Set(a));
// "KIXEYE, Victoria - Lead Game Designer": the level belongs to the part that holds the job title, not the company.
function titlePart(line, phrase) {
  const last = String(phrase || "").split(" ").pop();
  const parts = String(line).replace(RANGE, " ").split(/\s[-–—|]\s|,|\(|\)/).map(x => x.trim()).filter(Boolean);
  return parts.find(x => normTitle(x).split(" ").indexOf(last) >= 0) || line;
}
// The person's own corrections, kept in the browser: { add:[codes], remove:[codes], levelMin, levelMax, creds:[ids], noCreds:[ids],
// onlyDomain, includePast, block:[codes], blockTitles:[keys] }
function applyEdits(p, e) {
  if (!e) return p;
  const q = Object.assign({}, p);
  q.targets = uniq(p.targets.concat(e.add || [], e.includePast ? p.past : [])).filter(c => (e.remove || []).indexOf(c) < 0);
  if (e.levelMin) q.levelMin = e.levelMin;
  if (e.levelMax) q.levelMax = e.levelMax;
  q.creds = uniq(p.creds.concat(e.creds || [])).filter(c => (e.noCreds || []).indexOf(c) < 0);
  q.onlyDomain = !!e.onlyDomain;
  q.block = e.block || []; q.blockTitles = e.blockTitles || [];
  return q;
}
const occName = (c, dict) => (dict.occ[c] || [c])[0];

// ---------- matching
const eqOf = (c, dict) => (dict.occ[c] && dict.occ[c][3] === "eq") ? dict.occ[c][2] : c;
const soc = c => c.slice(0, 7);   // detailed SOC, without O*NET's .01 split
// The person's own field words: distinctive words of their job phrase, plus the field's other form
// ("accountant" -> "accounting", "nurse" -> "nursing"). Exact words, not stems: "mechanic" must not match "mechanical".
// Broad words (service, sales, product...) and generic job nouns (manager, engineer...) never count.
const FIELD_FORMS = { accountant: ["accounting"], nurse: ["nursing"], lawyer: ["legal"], attorney: ["legal"], counsel: ["legal"],
  paralegal: ["legal"], recruiter: ["recruiting", "talent acquisition"], teacher: ["teaching"], pharmacist: ["pharmacy"],
  auditor: ["audit"], electrician: ["electrical"], plumber: ["plumbing"], chef: ["culinary", "kitchen"], cook: ["culinary", "kitchen"],
  producer: ["production"], writer: ["writing"], editor: ["editorial"], therapist: ["therapy"], payroll: ["payroll"] };
const BROAD = new Set("service services customer client sales operation operations business product project program account support quality system systems technical digital general office professional senior junior staff lead chief head assistant associate global regional national".split(" "));
function fieldWords(p) {
  const out = [];
  for (const ph of p.phrases || []) for (const w of String(ph).split(" "))
    if (w.length >= 4 && !GENERIC_ONE.has(w) && !BROAD.has(w)) out.push(w, ...(FIELD_FORMS[w] || []));
  return uniq(out);
}
function hasFieldWord(title, words) {
  if (!words.length) return false;
  const t = " " + normTitle(title) + " ";
  return words.some(w => t.indexOf(" " + w + " ") >= 0);
}
function occScore(j, p, dict) {
  const oc = j.oc || [], stems = p._fw || (p._fw = fieldWords(p));
  if (!oc.length) {
    // A title O*NET can't read: accept it only if it names the person's own field ("Technical Accounting Policy Manager").
    return hasFieldWord(j.title, stems) ? { s: 0.6, why: "words" } : { s: 0, why: "unknown" };
  }
  if ((p.block || []).indexOf(oc[0]) >= 0) return { s: 0, why: "blocked" };
  const T = new Set(p.targets.map(c => eqOf(c, dict)));
  const same = c => T.has(eqOf(c, dict));
  if (same(oc[0])) return { s: j.ocf >= 0.7 ? 1 : j.ocf >= 0.5 ? 0.85 : 0.6, why: "same" };
  // Same detailed SOC, one direction only: "Registered Nurses" (.00) includes "Critical Care Nurses" (.03),
  // but "Video Game Designers" (.01) does not include "Web and Digital Interface Designers" (.00).
  // O*NET's .00 is not always the parent ("Web and Digital Interface Designers" is a sibling of "Video Game Designers"),
  // so the specialisation must also share the field's own distinctive word ("nurse" yes, generic "designer" no).
  const Tsoc = new Set(Array.from(T).filter(c => /\.00$/.test(c)).map(soc));
  if (!/^hg-/.test(oc[0]) && !/\.00$/.test(oc[0]) && Tsoc.has(soc(oc[0]))) {
    const words = c => normTitle(occName(c, dict)).split(" ").filter(w => w.length >= 4 && !GENERIC_ONE.has(w) && !BROAD.has(w) && w !== "and");
    const parent = Array.from(T).find(c => soc(c) === soc(oc[0]));
    if (parent && words(oc[0]).some(w => words(parent).indexOf(w) >= 0)) return { s: 0.7, why: "close" };
  }
  // Managing or leading your own field ("Manager, Lease Accounting", "Nurse Manager"): a management occupation
  // whose title names your field.
  if (/^11-/.test(oc[0]) && hasFieldWord(j.title, stems)) return { s: 0.7, why: "manage" };
  return { s: 0, why: "field" };
}
// TF-IDF cosine between the resume and each job; absolute, not divided by the best job in the list.
const SIM_STOP = new Set("the and for with you your our are will this that from have has can all who team work role job about into their they them what when where which while also more most other over such than then there these those through very was were would able ability strong including experience years year responsible company position candidate candidates new using use help join make well across within based ensure etc".split(" "));
function tfidfSims(resume, jobs) {
  const tok = t => (fold(t).match(/[a-z][a-z0-9+#]{2,}/g) || []).filter(w => !SIM_STOP.has(w));
  const docs = jobs.map(j => tok(((j.title || "") + " ").repeat(2) + (j.description || "")));
  const df = new Map(); for (const d of docs) for (const w of new Set(d)) df.set(w, (df.get(w) || 0) + 1);
  const N = docs.length || 1, idf = w => Math.log((N + 1) / ((df.get(w) || 0) + 1)) + 1;
  const vec = ws => { const m = new Map(); for (const w of ws) m.set(w, (m.get(w) || 0) + 1);
    let n = 0; for (const [w, c] of m) { const x = (1 + Math.log(c)) * idf(w); m.set(w, x); n += x * x; } return [m, Math.sqrt(n) || 1]; };
  const rv = vec(tok(resume));
  return docs.map(d => { const v = vec(d); let dot = 0; for (const [w, x] of v[0]) if (rv[0].has(w)) dot += x * rv[0].get(w); return dot / (v[1] * rv[1]); });
}
const HIDE_LABEL = { field: "a different field", unknown: "a field we couldn't read", blocked: "a field you turned off",
  junior: "too junior", senior: "too senior", years: "asking for more years than you have", cred: "needing a licence you don't list",
  where: "not open where you live", domain: "outside your industry", title: "titles you turned off" };

// pool: jobs that already passed the page's own filters (location, work style, age, keywords).
// o: { dict, now, mode: "best"|"newest", halfLifeDays, userCountry, evalOf(j) -> {arrangement, fit}, keyOf(j) -> role key }
function gateAndRank(pool, p, o) {
  const dict = o.dict, now = o.now || Date.now(), DAY = 86400000;
  // A repost doesn't reset the clock: a role's age is the earliest any copy of it was seen.
  const born = new Map();
  for (const j of pool) {
    const t = Math.min(j.posted || Infinity, Date.parse(j.firstSeen || "") || Infinity), k = o.keyOf ? o.keyOf(j) : j.id;
    if (!(born.get(k) <= t)) born.set(k, t);
  }
  const shown = [], hidden = {}, why = {};
  const hide = (j, r) => { hidden[r] = (hidden[r] || 0) + 1; why[j.id] = r; };
  for (const j of pool) {
    if ((p.blockTitles || []).indexOf(titleKeyOf(j.title)) >= 0) { hide(j, "title"); continue; }
    const os = occScore(j, p, dict);
    if (!os.s) { hide(j, os.why === "blocked" ? "blocked" : os.why === "unknown" ? "unknown" : "field"); continue; }
    // Level: explicit words gate. A title with no level word ("Registered Nurse") passes for anyone up to manager.
    let lf = 1;
    const lv = j.lv || 2;
    if (lv === 2 && !/\b(mid|ii|intermediate)\b/i.test(j.title)) { if (p.level > 5) { hide(j, "junior"); continue; } lf = 0.9; }
    else if (lv < p.levelMin) { hide(j, "junior"); continue; }
    else if (lv > p.levelMax) { hide(j, "senior"); continue; }
    else lf = lv === p.level ? 1 : 0.9;
    if (j.yr && p.totalYears && j.yr > p.totalYears + 2) { hide(j, "years"); continue; }
    if ((j.cr || []).some(c => p.creds.indexOf(c) < 0)) { hide(j, "cred"); continue; }
    const e = o.evalOf ? o.evalOf(j) : { arrangement: "", fit: "" };
    if (e.arrangement === "remote" && o.userCountry && j.el && j.el.length && j.el.indexOf(o.userCountry) < 0) { hide(j, "where"); continue; }
    if (p.onlyDomain && p.domain && j.dom !== p.domain) { hide(j, "domain"); continue; }
    const b = born.get(o.keyOf ? o.keyOf(j) : j.id);
    const age = isFinite(b) ? Math.max(0, (now - b) / DAY) : 365;
    shown.push(Object.assign({}, j, { _os: os.s, _lf: lf, _age: age, _fit: e.fit }));
  }
  const sims = tfidfSims(p.words, shown), half = o.halfLifeDays || 21;
  shown.forEach((j, i) => {
    j._sim = sims[i];
    j._match = j._os * j._lf * (0.6 + 0.4 * Math.min(1, sims[i] / 0.25));
    const fresh = Math.pow(0.5, j._age / half);
    const offDomain = p.domain && j.dom !== p.domain;
    j.tier = j._os >= 0.85 && j._lf >= 0.9 && j._fit !== "Location not stated" && !offDomain ? 2 : 1;
    j.score = j._match * (0.65 + 0.35 * fresh);
    const lv = j.lv || 2;
    j.reason = occName((j.oc && j.oc[0]) || p.targets[0] || "", dict) + (lv !== 2 ? " · " + LEVEL_NAMES[lv] : "");
  });
  if (o.mode === "newest") shown.sort((a, b) => (b.tier - a.tier) || (a._age - b._age));
  else shown.sort((a, b) => (b.tier - a.tier) || (b.score - a.score));
  return { list: shown, hidden, why };
}
const titleKeyOf = t => normTitle(t).replace(LEVEL_WORDS, " ").replace(/\s+/g, " ").trim();
function describeHidden(hidden) {
  return Object.keys(hidden).sort((a, b) => hidden[b] - hidden[a]).map(k => hidden[k].toLocaleString() + " " + (HIDE_LABEL[k] || k));
}

root.HGRel = { normTitle, matchTitle, levelOf, yearsRequired, credentialsRequired, credentialsHeld, remoteEligibility, enrichJob,
  profileFromResume, applyEdits, gateAndRank, describeHidden, tfidfSims, occName, titleKeyOf, LEVEL_NAMES, CREDENTIALS, HIDE_LABEL };
})(typeof globalThis !== "undefined" ? globalThis : this);
