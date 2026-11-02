"""Turn a raw response body into a minimal job index: id, title, url, posted, updated.

Parsing is deliberately strict about required fields: if a source changes shape,
`parse()` raises SchemaError and the recorder logs it. That IS the schema check.
Timestamps are normalized to ISO-8601 UTC strings (or None when the source has none).
"""
import json
import email.utils
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


class SchemaError(Exception):
    pass


def _iso(v):
    """Accept ISO strings, unix seconds, unix ms, or RFC-822 dates."""
    if v in (None, "", 0):
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            n = float(v)
            if n > 1e12:  # milliseconds
                n /= 1000
            return datetime.fromtimestamp(n, timezone.utc).isoformat(timespec="seconds")
        s = str(v).strip()
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            d = email.utils.parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def _need(obj, key, kind):
    if not isinstance(obj, dict) or key not in obj:
        raise SchemaError(f"{kind}: missing '{key}'")
    return obj[key]


def _job(id_, title, url, posted=None, updated=None):
    if id_ in (None, ""):
        raise SchemaError("job without id")
    return {"id": str(id_), "title": title or "", "url": url or "",
            "posted": _iso(posted), "updated": _iso(updated)}


def p_greenhouse(d):
    return [_job(j["id"], j.get("title"), j.get("absolute_url"),
                 j.get("first_published"), j.get("updated_at"))
            for j in _need(d, "jobs", "greenhouse")]


def p_lever(d):
    if not isinstance(d, list):
        raise SchemaError("lever: expected list")
    return [_job(j["id"], j.get("text"), j.get("hostedUrl"), j.get("createdAt"))
            for j in d]


def p_ashby(d):
    return [_job(j["id"], j.get("title"), j.get("jobUrl"),
                 j.get("publishedAt"), j.get("updatedAt"))
            for j in _need(d, "jobs", "ashby")]


def p_smartrecruiters(d):
    return [_job(j["id"], j.get("name"), j.get("ref"), j.get("releasedDate"))
            for j in _need(d, "content", "smartrecruiters")]


def p_remotive(d):
    return [_job(j["id"], j.get("title"), j.get("url"), j.get("publication_date"))
            for j in _need(d, "jobs", "remotive")]


def p_arbeitnow(d):
    return [_job(j["slug"], j.get("title"), j.get("url"), j.get("created_at"))
            for j in _need(d, "data", "arbeitnow")]


def p_jobicy(d):
    return [_job(j["id"], j.get("jobTitle"), j.get("url"), j.get("pubDate"))
            for j in _need(d, "jobs", "jobicy")]


def p_himalayas(d):
    return [_job(j.get("guid") or j.get("applicationLink"), j.get("title"),
                 j.get("applicationLink"), j.get("pubDate"))
            for j in _need(d, "jobs", "himalayas")]


def p_remoteok(d):
    if not isinstance(d, list):
        raise SchemaError("remoteok: expected list")
    # first element is a legal notice, not a job
    return [_job(j["id"], j.get("position"), j.get("url"), j.get("epoch") or j.get("date"))
            for j in d if isinstance(j, dict) and "id" in j]


def p_adzuna(d):
    return [_job(j["id"], j.get("title"), j.get("redirect_url"), j.get("created"))
            for j in _need(d, "results", "adzuna")]


def p_rss(body):
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise SchemaError(f"rss: {e}")
    items = root.findall(".//item")
    if root.find(".//channel") is None:
        raise SchemaError("rss: no channel")
    return [_job(i.findtext("guid") or i.findtext("link"), i.findtext("title"),
                 i.findtext("link"), i.findtext("pubDate")) for i in items]


JSON_PARSERS = {
    "greenhouse": p_greenhouse, "lever": p_lever, "ashby": p_ashby,
    "smartrecruiters": p_smartrecruiters, "remotive": p_remotive,
    "arbeitnow": p_arbeitnow, "jobicy": p_jobicy, "himalayas": p_himalayas,
    "remoteok": p_remoteok, "adzuna": p_adzuna,
}


def parse(kind, body: bytes):
    if kind == "wwr":
        return p_rss(body)
    try:
        d = json.loads(body)
    except ValueError as e:
        raise SchemaError(f"{kind}: not JSON ({e})")
    try:
        return JSON_PARSERS[kind](d)
    except (KeyError, TypeError) as e:
        raise SchemaError(f"{kind}: job missing field {e}")
