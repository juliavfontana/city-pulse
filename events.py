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


def save(city: str, df: pd.DataFrame, week: str):
    data_dir = Path("data") / city
    data_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(data_dir / f"events_{week}.csv", index=False)

    # One row per event: latest details, plus the first and last week it was in the feed
    path = data_dir / "events_all.csv"
    df = df.assign(first_seen=week, last_seen=week)
    if path.exists():
        old = pd.read_csv(path)
        first = dict(zip(old["uid"], old["first_seen"]))
        df["first_seen"] = df["uid"].map(first).fillna(week)
        df = pd.concat([old[~old["uid"].isin(df["uid"])], df], ignore_index=True)
    df.sort_values("start").to_csv(path, index=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=list(LUMA_FEEDS), help="One city (default: all)")
    args = parser.parse_args()

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
        unknown = sorted(set(df.loc[df["venue_type"] == UNKNOWN_VENUE, "location"].str[:60]))
        if unknown:
            print(f"  Unclassified venues (add to venues.csv): {unknown}")


if __name__ == "__main__":
    main()
