"""
City Cultural Pulse — Luma events collector
Pulls each city's Luma Discover iCal feed (the official "Add iCal Subscription"
feed of upcoming approved events), tags topics, venue type, area and time slot,
and keeps a running history.

Usage:
    python3 events.py                  # all cities in LUMA_FEEDS
    python3 events.py --city berlin

Outputs per city to data/<city>/:
    events_YYYY-WXX.csv   — this week's feed snapshot
    events_all.csv        — every event seen, one row per event (latest details,
                            first and last week it appeared in the feed)

For events whose address is hidden until approval, venue_hint keeps what the
host says publicly about the place ("a loft in SoHo"), and described_type comes
from data/event_venues.csv, filled in by hand from those hints. Addresses are
never looked up elsewhere: hosts hide them on purpose.
"""

import argparse
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from collect import current_week
from config import (CITIES, LUMA_FEEDS, EVENT_TOPICS, TECH_TOPICS, VENUE_TYPES,
                    HIDDEN_VENUE, UNKNOWN_VENUE)

ICS_URL = "https://api.luma.com/ics/get"
HEADERS = {"User-Agent": "city-pulse/1.0 (weekly research; iCal subscription)"}
TIMEZONES = {"berlin": "Europe/Berlin", "new_york": "America/New_York", "paris": "Europe/Paris"}

# Approximate: Berlin postcodes don't follow district lines exactly.
BERLIN_AREAS = {
    "Kreuzberg": "10961 10963 10965 10967 10969 10997 10999",
    "Neukölln": "12043 12045 12047 12049 12051 12053 12055 12057 12059",
    "Friedrichshain": "10243 10245 10247 10249",
    "Mitte": "10115 10117 10119 10178 10179 10785 10787",
    "Wedding / Gesundbrunnen": "13347 13349 13351 13353 13355 13357 13359",
    "Moabit / Tiergarten": "10551 10553 10555 10557 10559",
    "Prenzlauer Berg": "10405 10407 10409 10435 10437 10439",
    "Treptow": "12435 12437",
    "Schöneberg / Tempelhof": "10777 10779 10781 10783 10823 10825 10827 10829 12099 12101 12103 12105 12107",
    "Charlottenburg / Wilmersdorf": "10585 10587 10589 10623 10625 10627 10629 10707 10709 10711 10713 10715 10717 10719",
    "Lichtenberg": "10365 10367 10369",
}
BERLIN_AREAS = {pc: a for a, pcs in BERLIN_AREAS.items() for pc in pcs.split()}
# Words that describe a place, for hints from hidden-address events' own text.
VENUE_WORDS = re.compile(
    r"\b(venue|loft|studio|rooftop|offices?|hq|headquarters|campus|gallery|galerie|caf[eé]|bar|restaurant|"
    r"private home|our home|apartment|warehouse|refinery|garden|hotel|space in|coworking|library|museum)\b", re.I)
EVENT_VENUES = Path("data") / "event_venues.csv"  # uid → described_type, filled in by hand
LOWER_MANHATTAN = {10002, 10003, 10004, 10005, 10006, 10007, 10009, 10012, 10013, 10014, 10038, 10280, 10282}


# ── Parsing ───────────────────────────────────────────────────────────────────

def unescape(s: str) -> str:
    return s.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_ics(text: str) -> list:
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold continuation lines
    events = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        f = {}
        for line in block.splitlines():
            m = re.match(r"([A-Z-]+)(;[^:]*)?:(.*)", line)
            if m:
                f.setdefault(m.group(1), (m.group(2) or "", m.group(3)))
        get = lambda k: unescape(f.get(k, ("", ""))[1])
        desc = get("DESCRIPTION")
        org = re.search(r'CN="([^"]*)"', f.get("ORGANIZER", ("", ""))[0])
        hosted = re.search(r"Hosted by (.+)", desc)
        url = re.search(r"https://luma\.com/\S+", desc)
        geo = re.match(r"([-\d.]+);([-\d.]+)", get("GEO"))
        events.append({
            "uid": get("UID"),
            "start": pd.to_datetime(get("DTSTART"), utc=True),
            "end": pd.to_datetime(get("DTEND"), utc=True) if get("DTEND") else pd.NaT,
            "title": get("SUMMARY"),
            "organizer": org.group(1) if org else "",
            "hosted_by": hosted.group(1).strip() if hosted else "",
            "location": get("LOCATION"),
            "lat": float(geo.group(1)) if geo else None,
            "lon": float(geo.group(2)) if geo else None,
            "url": url.group(0) if url else "",
            "description": desc,
        })
    return events


# ── Tagging ───────────────────────────────────────────────────────────────────

def load_venues() -> dict:
    """{(city, venue name lowercased): type} from venues.csv."""
    path = Path("venues.csv")
    if not path.exists():
        return {}
    v = pd.read_csv(path)
    return {(r.city, r.venue.strip().lower()): r.type for r in v.itertuples()}


def venue_name(location: str) -> str:
    """
    The venue is the part before the first comma, unless that part is a street
    address: starting with a number (US: "456 W Broadway") or a street name with
    a house number (DE: "Wiener Str. 10"; but "Pier 57" is a venue).
    """
    if not location or location.startswith("https://luma.com"):
        return ""
    first = location.split(",")[0].strip()
    street = r"(str\.|straße|strasse|platz|pl\.|allee|damm|weg|ufer|ring|gasse|chaussee)\s*\d+[a-z]?(-\d+)?$"
    return "" if re.match(r"\d", first) or re.search(street, first.lower()) else first


def venue_type(city: str, location: str, venue: str, known: dict) -> str:
    if not location or location.startswith("https://luma.com"):
        return HIDDEN_VENUE
    key = (venue or location.split(",")[0]).strip().lower()  # venue name, or street address if unnamed
    if (city, key) in known:
        return known[(city, key)]
    low = location.lower()
    for name, rx in VENUE_TYPES:
        if re.search(rx, low):
            return name
    return UNKNOWN_VENUE


def suggest_venue_type(title: str, description: str) -> str:
    """For an unclassified venue, a guess from the event's own text (the venue
    rules applied to title + description). Shown as a suggestion only; nothing is
    classified until it's added to venues.csv."""
    body = "\n".join(l for l in description.split("\n") if not l.startswith("Get up-to-date information"))
    low = f"{title} {body[:600]}".lower()
    for name, rx in VENUE_TYPES:
        if name != "Online" and re.search(rx, low):
            return name
    return ""


def venue_hint(title: str, description: str) -> str:
    """Up to two snippets where the host's own public text describes the venue."""
    body = "\n".join(l for l in description.split("\n")
                     if not l.startswith(("Get up-to-date information", "Hosted by")))
    text = re.sub(r"\s+", " ", f"{title}. {body}")
    snippets, last_end = [], -1
    for m in VENUE_WORDS.finditer(text):
        start, end = max(0, m.start() - 60), m.end() + 60
        if start < last_end:  # overlaps the previous snippet
            continue
        snippets.append(text[start:end].strip())
        last_end = end
    return " … ".join(snippets[:2])


def load_event_venues() -> dict:
    if not EVENT_VENUES.exists():
        return {}
    df = pd.read_csv(EVENT_VENUES).dropna(subset=["described_type"])
    return dict(zip(df["uid"], df["described_type"]))


def area(city: str, location: str) -> str:
    if city == "berlin":
        m = re.search(r"\b(1[0-4]\d{3})\b", location)
        return BERLIN_AREAS.get(m.group(1), "Other Berlin") if m else ""
    if city == "new_york":
        m = re.search(r"NY (\d{5})", location)
        if not m:
            return ""
        z = int(m.group(1))
        if "Brooklyn" in location or 11200 <= z <= 11256:
            return "Brooklyn"
        if 10000 <= z <= 10299:
            return "Lower Manhattan" if z in LOWER_MANHATTAN else "Midtown & Upper Manhattan"
        if 11100 <= z <= 11699:
            return "Queens"
        return "Other NY"
    return ""


def slot(local) -> str:
    return "Morning" if local.hour < 12 else "Afternoon" if local.hour < 17 else "Evening"


def tag(df: pd.DataFrame, city: str) -> pd.DataFrame:
    known = load_venues()
    tz = ZoneInfo(TIMEZONES.get(city, "UTC"))
    text = (df["title"] + " " + df["description"].str[:600]).str.lower()
    df["topics"] = text.map(lambda t: ";".join(n for n, rx in EVENT_TOPICS.items() if re.search(rx, t)))
    df["tech"] = df["topics"].map(lambda ts: bool(set(ts.split(";")) & TECH_TOPICS))
    df["venue"] = df["location"].map(venue_name)
    df["venue_type"] = [venue_type(city, l, v, known) for l, v in zip(df["location"], df["venue"])]
    hidden = df["venue_type"] == HIDDEN_VENUE
    df["venue_hint"] = ""
    df.loc[hidden, "venue_hint"] = [venue_hint(a, b) for a, b in zip(df.loc[hidden, "title"], df.loc[hidden, "description"])]
    described = load_event_venues()
    df["described_type"] = [described.get(u, "") if h else "" for u, h in zip(df["uid"], hidden)]
    unknown = df["venue_type"] == UNKNOWN_VENUE
    df["venue_suggestion"] = ""
    df.loc[unknown, "venue_suggestion"] = [suggest_venue_type(a, b) for a, b in
                                           zip(df.loc[unknown, "title"], df.loc[unknown, "description"])]
    df["area"] = df["location"].map(lambda l: area(city, l))
    local = df["start"].dt.tz_convert(tz)
    df["slot"] = local.map(slot)
    df["weekday"] = local.dt.day_name()
    return df


# ── Collect & save ────────────────────────────────────────────────────────────

def collect_events(city: str) -> pd.DataFrame:
    r = requests.get(ICS_URL, params={"entity": "discover", "id": LUMA_FEEDS[city]},
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    df = pd.DataFrame(parse_ics(r.text))
    if df.empty:
        return df
    df.insert(1, "city", city)
    return tag(df, city)


def retag(df: pd.DataFrame, city: str) -> pd.DataFrame:
    """Re-applies tag() to stored events, so changes to venues.csv,
    data/event_venues.csv or the topic rules reach events already collected."""
    if df.empty:
        return df
    df = df.copy()
    for col in ["title", "description", "location"]:
        df[col] = df[col].fillna("")
    for col in ["start", "end"]:
        df[col] = pd.to_datetime(df[col], utc=True)
    return tag(df, city)


def save(city: str, df: pd.DataFrame, week: str):
    data_dir = Path("data") / city
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(data_dir / f"events_{week}.csv", index=False)

    # One row per event: latest details, plus the first and last week it was in the feed
    path = data_dir / "events_all.csv"
    df = df.assign(first_seen=week, last_seen=week)
    if path.exists():
        old = retag(pd.read_csv(path), city)
        first = dict(zip(old["uid"], old["first_seen"]))
        df["first_seen"] = df["uid"].map(first).fillna(week)
        df = pd.concat([old[~old["uid"].isin(df["uid"])], df], ignore_index=True)
    df.sort_values("start").to_csv(path, index=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=list(LUMA_FEEDS), help="One city (default: all)")
    parser.add_argument("--retag", action="store_true",
                        help="Re-tag stored events (after editing venues.csv or topic rules); no fetching")
    args = parser.parse_args()

    if args.retag:
        for city in [args.city] if args.city else LUMA_FEEDS:
            for path in sorted((Path("data") / city).glob("events_*.csv")):
                old = pd.read_csv(path)
                new = retag(old, city)
                new.to_csv(path, index=False)
                changed = (old["venue_type"].fillna("") != new["venue_type"]).sum() if "venue_type" in old else len(new)
                retopic = (old["tech"] != new["tech"]).sum() if "tech" in old else 0
                print(f"[Events] Re-tagged {path}: {changed} venue types changed, {retopic} tech/startup flags changed")
        return

    week = current_week()
    for city in [args.city] if args.city else LUMA_FEEDS:
        name = CITIES[city]["display_name"]
        df = collect_events(city)
        if df.empty:
            print(f"[Events] {name}: feed returned no events")
            continue
        save(city, df, week)
        t = df[df["tech"]]
        print(f"[Events] {name}: {len(df)} events ({df['start'].min():%b %d} → {df['start'].max():%b %d}), "
              f"{len(t)} tech/startup → data/{city}/events_{week}.csv")
        print("  Venue types (tech): " + ", ".join(f"{k} {v}" for k, v in t["venue_type"].value_counts().items()))
        unknown = df[df["venue_type"] == UNKNOWN_VENUE].drop_duplicates("location")
        if len(unknown):
            print(f"  {len(unknown)} unclassified venues. Check, then paste into venues.csv (city,venue,type):")
            for r in unknown.itertuples():
                key = r.venue or r.location.split(",")[0].strip()
                print(f"    {city},{key},{r.venue_suggestion or '?'}")


if __name__ == "__main__":
    main()
