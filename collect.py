"""
City Cultural Pulse — data collector
Collects weekly search trend + autocomplete signal data across tech, culture,
fashion, food, and travel for a given city.

Usage:
    python3 collect.py --city berlin          # live run
    python3 collect.py --city berlin --test   # dry run, no API calls
    python3 collect.py --city new_york
    python3 collect.py --city paris
"""

import os
import time
import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import pandas as pd

from config import CITIES, get_seeds, TRENDS_ANCHOR, TRENDS_TIMEFRAME, RISING_CATEGORIES, RISING_TIMEFRAME

# ── Headers ───────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── Week label ────────────────────────────────────────────────────────────────

def current_week() -> str:
    """
    ISO week of the most recent Sunday (today, if it's Sunday), e.g. "2026-W44".
    The Sunday run and a Monday catch-up run (Mac asleep on Sunday) get the same
    label. Before 2026-W45 labels used %W, which matches this for every run so far
    (all on Mondays) but would have given a Sunday run the previous week's label.
    """
    today = date.today()
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    year, week, _ = sunday.isocalendar()
    return f"{year}-W{week:02d}"


# ── Google Trends ─────────────────────────────────────────────────────────────

def search_key(query: str) -> str:
    """Google treats case and word order as the same search ("Berlin theater" = "Theater Berlin")."""
    return " ".join(sorted(query.lower().split()))


def trends_history(city_key: str) -> pd.DataFrame:
    path = Path("data") / city_key / "trends_all.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "anchor_score" not in df.columns:
        return pd.DataFrame()
    return df[df["source"] == "google_trends_anchored"]


def fetch_with_retry(pytrends, fetch, keywords: list, timeframe: str, geo: str, cat: int = 0):
    """Google rate-limits Trends (HTTP 429) — back off and retry."""
    for attempt in range(4):
        try:
            pytrends.build_payload(keywords, cat=cat, timeframe=timeframe, geo=geo)
            return fetch()
        except Exception as e:
            wait = 60 * (attempt + 1)
            print(f"    Error: {e} — retrying in {wait}s")
            time.sleep(wait)
    return None

def collect_trends(city_key: str, dry_run: bool = False) -> pd.DataFrame:
    """
    Pulls weekly interest-over-time from Google Trends.
    Geo-filtered to the city's country/region. Each batch holds 4 seeds plus
    TRENDS_ANCHOR, whose score is stored on every row as anchor_score so
    analysis can put all batches (and cities) on the same relative scale.
    """
    city = CITIES[city_key]
    seed_queries = get_seeds(city_key)
    display = city["display_name"]
    week_tag = current_week()

    print(f"\n[Trends] Starting Google Trends collection for {display}...")

    if dry_run:
        rows = []
        for domain, queries in seed_queries.items():
            for q in queries:
                rows.append({
                    "week": datetime.now(timezone.utc).strftime("%Y-%W"),
                    "city": city_key,
                    "domain": domain,
                    "query": q,
                    "interest_score": 50,
                    "source": "trends_dry_run",
                })
        print(f"  [Trends] Dry run — {len(rows)} fake rows")
        return pd.DataFrame(rows)

    try:
        from pytrends.request import TrendReq
    except ImportError:
        print("  [Trends] pytrends not installed. Run: pip3 install pytrends")
        return pd.DataFrame()

    pytrends = TrendReq(
        hl=city["hl_primary"],
        tz=city["tz"],
        requests_args={"headers": HEADERS}
    )

    # Each distinct search is fetched once, and its values are copied to every
    # (domain, query) that uses it, so the output rows are the same as before.
    searches = {}
    for domain, queries in seed_queries.items():
        for q in queries:
            searches.setdefault(search_key(q), []).append((domain, q))

    # Terms with zero interest in everything collected so far are skipped (too
    # little search volume to score), except every 4th week so a term that picks
    # up still gets noticed. Old rows stay in trends_all.csv.
    history = trends_history(city_key)
    quiet = set()
    if not history.empty and int(week_tag[-2:]) % 4 != 0:
        totals = history.groupby("query")["interest_score"].sum()
        quiet = set(totals[totals == 0].index)

    # Trends scales each batch to its biggest term, rounded to whole numbers, so
    # small terms batched with big ones round to 0. Ordering by past level keeps
    # similar-sized terms (and so the anchor's scale) together.
    level = {}
    if not history.empty:
        ratio = history["interest_score"] / history["anchor_score"].where(history["anchor_score"] > 0)
        level = ratio.groupby(history["query"]).mean().fillna(0).to_dict()

    fetch = [pairs[0][1] for pairs in searches.values() if not all(q in quiet for _, q in pairs)]
    fetch.sort(key=lambda q: -max(level.get(dq, 0) for _, dq in searches[search_key(q)]))
    print(f"  {len(fetch)} distinct searches ({len(searches) - len(fetch)} with no measurable interest skipped this week)")

    rows = []

    for i in range(0, len(fetch), 4):
        batch = fetch[i:i+4]
        print(f"  Fetching trends: {batch}")

        df = fetch_with_retry(pytrends, pytrends.interest_over_time,
                              batch + [TRENDS_ANCHOR], TRENDS_TIMEFRAME, city["geo_trends"])

        if df is None or df.empty:
            print(f"    No data returned")
            continue

        df = df.drop(columns=["isPartial"], errors="ignore").reset_index()

        for q in batch:
            if q not in df.columns:
                continue
            for domain, query in searches[search_key(q)]:
                for _, row in df.iterrows():
                    rows.append({
                        "week": row["date"].strftime("%Y-%W"),
                        "date": row["date"].strftime("%Y-%m-%d"),
                        "city": city_key,
                        "geo": city["geo_trends"],
                        "domain": domain,
                        "query": query,
                        "interest_score": int(row[q]),
                        "anchor": TRENDS_ANCHOR,
                        "anchor_score": int(row[TRENDS_ANCHOR]),
                        "source": "google_trends_anchored",
                    })

        time.sleep(5)

    df_out = pd.DataFrame(rows)
    print(f"  [Trends] Collected {len(df_out)} rows")
    return df_out


def collect_rising(city_key: str, dry_run: bool = False) -> pd.DataFrame:
    """
    Per domain, pulls Google Trends' "rising" searches for the whole category in
    RISING_CATEGORIES (no keyword), in the city's region over RISING_TIMEFRAME.
    value = % growth; Google labels 5000%+ as "Breakout".
    """
    if dry_run:
        return pd.DataFrame()

    from pytrends.request import TrendReq

    city = CITIES[city_key]
    week_tag = current_week()
    pytrends = TrendReq(hl=city["hl_primary"], tz=city["tz"], requests_args={"headers": HEADERS})

    print(f"\n[Rising] Fetching rising related searches for {city['display_name']}...")
    rows = []
    for domain, (cat, cat_name) in RISING_CATEGORIES.items():
        try:
            result = fetch_with_retry(pytrends, pytrends.related_queries,
                                      [""], RISING_TIMEFRAME, city["geo_trends"], cat=cat)
            rising = (result or {}).get("", {}).get("rising")
        except Exception as e:  # pytrends fails on some empty responses
            print(f"    {cat_name}: {e}")
            rising = None
        n = 0 if rising is None else len(rising)
        print(f"  [{domain}] {cat_name} — {n} rising")
        for rank, r in enumerate(rising.itertuples() if n else [], start=1):
            rows.append({
                "week": week_tag,
                "city": city_key,
                "geo": city["geo_trends"],
                "domain": domain,
                "category": cat_name,
                "rank": rank,
                "query": r.query,
                "value": int(r.value),
                "timeframe": RISING_TIMEFRAME,
            })
        time.sleep(5)

    return pd.DataFrame(rows)


# ── Google Autocomplete ───────────────────────────────────────────────────────

def fetch_autocomplete(query: str, country: str, hl: str = "en") -> list:
    """
    Calls Google's suggest API for one query string.
    Returns a list of suggestion strings. No API key needed.
    """
    url = "https://suggestqueries.google.com/complete/search"
    params = {
        "client": "firefox",
        "q": query,
        "hl": hl,
        "gl": country,
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data[1] if len(data) > 1 else []
    except Exception as e:
        print(f"    Autocomplete error for '{query}': {e}")
    return []


def collect_autocomplete(city_key: str, dry_run: bool = False) -> pd.DataFrame:
    """
    For each seed query, fetches plain Google autocomplete suggestions.
    Every seed is run with English results, and again with local-language
    results (hl) for cities that have a local language.
    """
    city = CITIES[city_key]
    seed_queries = get_seeds(city_key)
    display = city["display_name"]
    country = city["geo_autocomplete"]
    week_tag = current_week()
    languages = [city["hl_primary"]] + ([city["hl_local"]] if city["hl_local"] else [])

    print(f"\n[Autocomplete] Starting collection for {display}...")

    if dry_run:
        fake = [
            {
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "city": city_key,
                "domain": "tech",
                "seed": f"AI companies in {display}",
                "suggestion": f"AI companies in {display} to work for",
                "source": "autocomplete_dry_run",
            },
            {
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "city": city_key,
                "domain": "culture",
                "seed": f"best clubs in {display}",
                "suggestion": f"best clubs in {display} 2025",
                "source": "autocomplete_dry_run",
            },
            {
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "city": city_key,
                "domain": "food",
                "seed": f"best restaurants {display}",
                "suggestion": f"best restaurants {display} 2025",
                "source": "autocomplete_dry_run",
            },
        ]
        for row in fake:
            row["week"] = week_tag
            row["hl"] = "en"
        print(f"  [Autocomplete] Dry run — {len(fake)} fake rows")
        return pd.DataFrame(fake)

    rows = []

    for domain, queries in seed_queries.items():
        for seed in queries:
            for hl in languages:
                suggestions = fetch_autocomplete(seed, country, hl=hl)
                for rank, s in enumerate(suggestions, start=1):
                    rows.append({
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                        "week": week_tag,
                        "city": city_key,
                        "domain": domain,
                        "seed": seed,
                        "hl": hl,
                        "rank": rank,  # position in the dropdown, from 2026-W45
                        "suggestion": s,
                        "source": f"autocomplete_{hl}",
                    })
                time.sleep(0.5)

            print(f"  [{domain}] '{seed}' done — {len(rows)} suggestions so far")

    df_out = pd.DataFrame(rows).drop_duplicates(subset=["hl", "suggestion"])
    print(f"  [Autocomplete] Collected {len(df_out)} unique suggestions")
    return df_out


# ── Save output ───────────────────────────────────────────────────────────────

def save_run(
    city_key: str,
    trends_df: pd.DataFrame,
    autocomplete_df: pd.DataFrame,
    rising_df: pd.DataFrame,
    dry_run: bool = False
):
    data_dir = Path("data") / city_key
    data_dir.mkdir(parents=True, exist_ok=True)

    week_tag = current_week()
    suffix = "_dryrun" if dry_run else ""

    trends_path = data_dir / f"trends_{week_tag}{suffix}.csv"
    auto_path   = data_dir / f"autocomplete_{week_tag}{suffix}.csv"

    trends_df.to_csv(trends_path, index=False)
    autocomplete_df.to_csv(auto_path, index=False)
    print(f"\n[Save] Trends       → {trends_path}")
    print(f"[Save] Autocomplete → {auto_path}")
    if not rising_df.empty:
        rising_path = data_dir / f"rising_{week_tag}.csv"
        rising_df.to_csv(rising_path, index=False)
        print(f"[Save] Rising       → {rising_path}")

    # Append to cumulative files. Dry runs never touch them, so fake rows
    # can't leak into analysis. Trends: each run re-fetches 12 months of weekly
    # data, so overlapping weeks are replaced by the newest values (per source,
    # so older unanchored rows are kept but never mixed in). Autocomplete:
    # every week's suggestions are kept, so change over time stays visible.
    if not dry_run:
        for path, df, dedup_cols in [
            (data_dir / "trends_all.csv",       trends_df,       ["source", "date", "domain", "query"]),
            (data_dir / "autocomplete_all.csv", autocomplete_df, ["week", "hl", "suggestion"]),
            (data_dir / "rising_all.csv",       rising_df,       ["week", "category", "query"]),
        ]:
            if df.empty:
                continue
            if path.exists() and path.stat().st_size > 1:
                existing = pd.read_csv(path)
                combined = pd.concat([existing, df])
                if all(c in combined.columns for c in dedup_cols):
                    combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
                combined.to_csv(path, index=False)
            else:
                df.to_csv(path, index=False)

        print(f"[Save] Cumulative files updated in {data_dir}/")

    meta = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "city": city_key,
        "week": week_tag,
        "dry_run": dry_run,
        "trends_rows": len(trends_df),
        "autocomplete_rows": len(autocomplete_df),
        "rising_rows": len(rising_df),
    }
    (data_dir / f"run_meta_{week_tag}{suffix}.json").write_text(
        json.dumps(meta, indent=2)
    )


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(city_key: str, trends_df: pd.DataFrame, autocomplete_df: pd.DataFrame):
    display = CITIES[city_key]["display_name"]

    print("\n" + "="*55)
    print(f"  COLLECTION SUMMARY — {display.upper()}")
    print("="*55)

    if not trends_df.empty and "interest_score" in trends_df.columns:
        print("\nTop trend scores this week:")
        top = (
            trends_df.groupby(["domain", "query"])["interest_score"]
            .max().reset_index()
            .sort_values("interest_score", ascending=False)
            .head(8)
        )
        for _, row in top.iterrows():
            print(f"  [{row['domain']:8}] {row['query']:<40} {row['interest_score']}")

    if not autocomplete_df.empty:
        print(f"\nAutocomplete suggestions: {len(autocomplete_df)}")
        print("\nSample suggestions per domain:")
        for domain in ["tech", "culture", "fashion", "food", "travel"]:
            sample = autocomplete_df[autocomplete_df["domain"] == domain].head(2)
            for _, row in sample.iterrows():
                print(f"  [{domain}] {row['suggestion'][:72]}")

    print("="*55)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--city",
        required=True,
        choices=list(CITIES.keys()),
        help="City to collect data for"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Dry run — no live API calls"
    )
    args = parser.parse_args()

    display = CITIES[args.city]["display_name"]
    print(f"\n{display} Cultural Pulse — collection run")
    print(f"Week : {current_week()}")
    print(f"Mode : {'DRY RUN' if args.test else 'LIVE'}")

    trends_df       = collect_trends(args.city, dry_run=args.test)
    autocomplete_df = collect_autocomplete(args.city, dry_run=args.test)
    rising_df       = collect_rising(args.city, dry_run=args.test)

    save_run(args.city, trends_df, autocomplete_df, rising_df, dry_run=args.test)
    print_summary(args.city, trends_df, autocomplete_df)


if __name__ == "__main__":
    main()
