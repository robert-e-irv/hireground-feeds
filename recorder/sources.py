"""Every Hire Ground source the recorder snapshots.

STUDIOS: 66 studio boards verified 2026-10-28 (Greenhouse, Lever, Ashby).
AGGREGATORS: board-wide feeds the app also pulls.
`redistributable` is the terms gate for the PUBLIC cache. The recorder measures
everything; the pipeline only publishes sources marked True (see DESIGN.md).
"""

STUDIOS = [  # name, category, [(ats, slug)], open roles when verified 2026-10-28
("2K","publisher",[("greenhouse","2k")],95),
("Avalanche Studios Group","studio",[("lever","avalanchestudios")],4),
("Bandai Namco Entertainment America","publisher",[("greenhouse","bandainamco")],0),
("ArenaNet","studio",[("ashby","arenanet")],5),
("Behaviour Interactive","studio",[("lever","bhvr")],26),
("Bit Reactor","studio",[("greenhouse","bitreactor")],0),
("Blackbird Interactive","studio",[("lever","blackbirdinteractive")],2),
("Bonfire Studios","studio",[("greenhouse","bonfirestudiosinc")],1),
("Bungie","studio",[("greenhouse","bungie")],5),
("Crystal Dynamics","studio",[("greenhouse","crystaldynamics")],11),
("Crytek","studio",[("lever","crytek")],5),
("Digital Eclipse","studio",[("greenhouse","digitaleclipse")],0),
("Digital Extremes","studio",[("greenhouse","digitalextremes")],10),
("Dream Games","studio",[("lever","dreamgames"),("greenhouse","dreamgames")],19),
("Epic Games","studio",[("greenhouse","epicgames")],145),
("Firaxis Games","studio",[("greenhouse","firaxis")],1),
("Firesprite","studio",[("greenhouse","firesprite")],1),
("Fortis Games","studio",[("greenhouse","fortisgames")],0),
("Gardens","studio",[("ashby","gardens")],1),
("Gearbox","studio",[("greenhouse","gearbox")],1),
("Hangar 13","studio",[("greenhouse","hangar13")],7),
("Haven Studios","studio",[("greenhouse","haven")],3),
("Housemarque","studio",[("greenhouse","housemarque")],5),
("HoYoverse","publisher",[("ashby","hoyoverse"),("greenhouse","hoyoverse")],20),
("Insomniac Games","studio",[("greenhouse","insomniac")],28),
("Jam City","studio",[("lever","jamcity")],15),
("Kabam","studio",[("lever","kabam")],23),
("Kolibri Games","studio",[("lever","kolibrigames")],5),
("KRAFTON","publisher",[("greenhouse","krafton")],56),
("Larian Studios","studio",[("lever","larian")],77),
("Mediatonic","studio",[("greenhouse","mediatonic")],0),
("Metacore","studio",[("greenhouse","metacore")],0),
("Mob Entertainment","studio",[("greenhouse","mobentertainment")],9),
("Mojang Studios","studio",[("greenhouse","mojangab")],7),
("Moon Active","studio",[("ashby","moonactive")],34),
("Naughty Dog","studio",[("greenhouse","naughtydog")],17),
("NetEase Games","publisher",[("greenhouse","neteasegames")],30),
("Nintendo of America","publisher",[("greenhouse","nintendo")],64),
("Peak Games","studio",[("lever","peakgames")],19),
("PlayStation (Sony Interactive Entertainment)","publisher",[("greenhouse","sonyinteractiveentertainmentglobal")],205),
("Playtika","publisher",[("greenhouse","playtikaltd")],14),
("Pocket Gems","studio",[("greenhouse","pocketgems")],0),
("Psyonix","studio",[("greenhouse","psyonix")],0),
("Riot Games","studio",[("greenhouse","riotgames")],184),
("Roblox","platform",[("greenhouse","roblox")],271),
("Rockstar Games","studio",[("greenhouse","rockstargames")],24),
("Scopely","publisher",[("greenhouse","scopely")],181),
("Second Dinner","studio",[("ashby","seconddinner")],1),
("Singularity 6","studio",[("greenhouse","singularitysix")],0),
("SkyBox Labs","studio",[("lever","skyboxlabs")],7),
("Socialpoint","studio",[("greenhouse","socialpoint")],0),
("Supercell","studio",[("ashby","supercell")],37),
("Take-Two Interactive","publisher",[("greenhouse","taketwo")],36),
("thatgamecompany","studio",[("ashby","thatgamecompany")],38),
("Theorycraft Games","studio",[("lever","theorycraftgames")],1),
("Tripledot Studios","studio",[("greenhouse","tripledotstudios")],95),
("Tripwire Interactive","studio",[("greenhouse","tripwireinteractive")],1),
("Turtle Rock Studios","studio",[("greenhouse","turtlerockstudios")],1),
("Unknown Worlds","studio",[("greenhouse","unknownworlds")],2),
("Visual Concepts","studio",[("greenhouse","visualconcepts")],4),
("Voodoo","publisher",[("ashby","voodoo")],100),
("Wooga","studio",[("greenhouse","wooga")],5),
("Discord","adjacent",[("greenhouse","discord")],46),
("Twitch","adjacent",[("greenhouse","twitch")],62),
("Immutable","adjacent",[("lever","immutable")],5),
("Hasbro (incl. Wizards of the Coast)","adjacent",[("greenhouse","hasbro")],113),
]


# kind, name, url, redistributable, notes
AGGREGATORS = [
    ("remotive", "Remotive", "https://remotive.com/api/remote-jobs", False,
     "Terms restrict republishing; measure only"),
    ("arbeitnow", "Arbeitnow", "https://www.arbeitnow.com/api/job-board-api", None,
     "Terms unreviewed"),
    ("jobicy", "Jobicy", "https://jobicy.com/api/v2/remote-jobs?count=100", None,
     "Terms unreviewed"),
    ("himalayas", "Himalayas", "https://himalayas.app/jobs/api?limit=100", None,
     "Terms unreviewed"),
    ("remoteok", "RemoteOK", "https://remoteok.com/api", None,
     "Requires attribution link back; terms unreviewed"),
    ("wwr", "We Work Remotely", "https://weworkremotely.com/remote-jobs.rss", False,
     "Terms restrict republishing; measure only"),
    ("adzuna", "Adzuna (CA)",
     "https://api.adzuna.com/v1/api/jobs/ca/search/1?results_per_page=50&what=game",
     False, "Needs ADZUNA_APP_ID/ADZUNA_APP_KEY secrets; terms restrict republishing"),
]

# Extra per-company boards on other ATSs already used by the app
EXTRA_BOARDS = [
    ("Ludia", "studio", [("smartrecruiters", "TBD14102022Ludia")], None),
]

ATS_REDISTRIBUTABLE = {"greenhouse": True, "lever": True, "ashby": True,
                       "smartrecruiters": True}

ATS_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
}


def verified_employers(path=None):
    """Non-games employer boards that passed verify_boards.py (see employers.py). Empty until verified."""
    import json
    from pathlib import Path
    p = Path(path) if path else Path(__file__).with_name("employers_verified.json")
    try:
        return json.loads(p.read_text())["kept"]
    except (OSError, ValueError, KeyError):
        return []


def all_sources():
    """Yield dicts: id, name, kind, url, redistributable, notes (+ industry for employer boards)."""
    seen = set()
    for name, cat, boards, _ in STUDIOS + EXTRA_BOARDS:
        for ats, slug in boards:
            seen.add(f"{ats}:{slug}".lower())
        for ats, slug in boards:
            yield {"id": f"{ats}:{slug}", "name": name, "category": cat, "kind": ats,
                   "url": ATS_URL[ats].format(slug=slug),
                   "redistributable": ATS_REDISTRIBUTABLE[ats], "notes": ""}
    for e in verified_employers():
        sid = f"{e['ats']}:{e['slug']}"
        if sid.lower() in seen:
            continue
        seen.add(sid.lower())
        yield {"id": sid, "name": e["name"], "category": "employer", "industry": e["industry"],
               "kind": e["ats"], "url": ATS_URL[e["ats"]].format(slug=e["slug"]),
               "redistributable": ATS_REDISTRIBUTABLE[e["ats"]], "notes": ""}
    for kind, name, url, redis, notes in AGGREGATORS:
        yield {"id": kind, "name": name, "category": "aggregator", "kind": kind,
               "url": url, "redistributable": redis, "notes": notes}
