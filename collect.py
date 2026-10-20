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
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd

from config import CITIES, get_seeds, TRENDS_ANCHOR, TRENDS_TIMEFRAME

# ── Headers ───────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── Google Trends ─────────────────────────────────────────────────────────────

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

    rows = []

    for domain, queries in seed_queries.items():
        for i in range(0, len(queries), 4):
            batch = queries[i:i+4]
            print(f"  [{domain}] Fetching trends: {batch}")

            # Google rate-limits Trends (HTTP 429) — back off and retry
            df = None
            for attempt in range(4):
                try:
                    pytrends.build_payload(
                        batch + [TRENDS_ANCHOR],
                        timeframe=TRENDS_TIMEFRAME,
                        geo=city["geo_trends"]
                    )
                    df = pytrends.interest_over_time()
                    break
                except Exception as e:
                    wait = 60 * (attempt + 1)
                    print(f"    Error: {e} — retrying in {wait}s")
                    time.sleep(wait)

            if df is None or df.empty:
                print(f"    No data returned")
                continue

            df = df.drop(columns=["isPartial"], errors="ignore").reset_index()

            for q in batch:
                if q not in df.columns:
                    continue
                for _, row in df.iterrows():
                    rows.append({
                        "week": row["date"].strftime("%Y-%W"),
                        "date": row["date"].strftime("%Y-%m-%d"),
                        "city": city_key,
                        "geo": city["geo_trends"],
                        "domain": domain,
                        "query": q,
                        "interest_score": int(row[q]),
                        "anchor": TRENDS_ANCHOR,
                        "anchor_score": int(row[TRENDS_ANCHOR]),
                        "source": "google_trends_anchored",
                    })

            time.sleep(5)

    df_out = pd.DataFrame(rows)
    print(f"  [Trends] Collected {len(df_out)} rows")
    return df_out


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
    week_tag = datetime.now(timezone.utc).strftime("%Y-W%W")
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
                for s in suggestions:
                    rows.append({
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                        "week": week_tag,
                        "city": city_key,
                        "domain": domain,
                        "seed": seed,
                        "hl": hl,
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
    dry_run: bool = False
):
    data_dir = Path("data") / city_key
    data_dir.mkdir(parents=True, exist_ok=True)

    week_tag = datetime.now(timezone.utc).strftime("%Y-W%W")
    suffix = "_dryrun" if dry_run else ""

    trends_path = data_dir / f"trends_{week_tag}{suffix}.csv"
    auto_path   = data_dir / f"autocomplete_{week_tag}{suffix}.csv"

    trends_df.to_csv(trends_path, index=False)
    autocomplete_df.to_csv(auto_path, index=False)
    print(f"\n[Save] Trends       → {trends_path}")
    print(f"[Save] Autocomplete → {auto_path}")

    # Append to cumulative files. Dry runs never touch them, so fake rows
    # can't leak into analysis. Trends: each run re-fetches 12 months of weekly
    # data, so overlapping weeks are replaced by the newest values (per source,
    # so older unanchored rows are kept but never mixed in). Autocomplete:
    # every week's suggestions are kept, so change over time stays visible.
    if not dry_run:
        for path, df, dedup_cols in [
            (data_dir / "trends_all.csv",       trends_df,       ["source", "date", "domain", "query"]),
            (data_dir / "autocomplete_all.csv", autocomplete_df, ["week", "hl", "suggestion"]),
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
    print("\nNext: run python3 analyze.py --city {city_key} once you have 2+ weeks of data.")


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
    print(f"Week : {datetime.now(timezone.utc).strftime('%Y-W%W')}")
    print(f"Mode : {'DRY RUN' if args.test else 'LIVE'}")

    trends_df       = collect_trends(args.city, dry_run=args.test)
    autocomplete_df = collect_autocomplete(args.city, dry_run=args.test)

    save_run(args.city, trends_df, autocomplete_df, dry_run=args.test)
    print_summary(args.city, trends_df, autocomplete_df)


if __name__ == "__main__":
    main()
