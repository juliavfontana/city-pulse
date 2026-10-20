# City Cultural Pulse

A weekly signal tracker monitoring what people search across tech, culture, fashion,
food, and travel in Berlin, New York, and Paris — and how those signals compare.

Built to surface how city identity shows up in search behaviour, and to predict
which topics are about to peak before they go mainstream.

---

## Project structure

```
city-pulse/
├── config.py               # all seed queries + city settings (start here)
├── collect.py              # data collection — Google Trends + Autocomplete
├── collect_berlin.py       # thin entry point for Berlin
├── collect_newyork.py      # thin entry point for New York
├── collect_paris.py        # thin entry point for Paris
├── analyze.py              # trend scoring + report generation
├── data/
│   ├── berlin/             # berlin CSVs (trends, autocomplete, cumulative)
│   ├── new_york/           # new york CSVs
│   └── paris/              # paris CSVs
├── reports/                # weekly reports, trend scores, comparison reports
└── README.md
```

---

## Setup (5 minutes)

### 1. Install dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install pytrends pandas numpy requests
```

Run the scripts with `.venv/bin/python` (e.g. `.venv/bin/python collect_berlin.py`).

### 2. Verify config

```bash
python3 config.py
```

Should print seed counts per city and domain — no API calls needed.

---

## Weekly workflow

### Step 1: Test run (no API calls)

```bash
python3 collect_berlin.py --test
python3 collect_newyork.py --test
python3 collect_paris.py --test
```

### Step 2: Live collection

```bash
python3 collect_berlin.py
python3 collect_newyork.py
python3 collect_paris.py
```

Or call directly with a city flag:

```bash
python3 collect.py --city berlin
python3 collect.py --city new_york
python3 collect.py --city paris
```

Outputs per city to `data/<city>/`:
- `trends_YYYY-WXX.csv` — Google Trends snapshot
- `autocomplete_YYYY-WXX.csv` — autocomplete suggestions
- `trends_all.csv` — cumulative trends (appended each run)
- `autocomplete_all.csv` — cumulative autocomplete; every week's suggestions are kept
  (deduplicated only within the same week + language), so you can compare weeks
- `run_meta_YYYY-WXX.json` — run metadata

### Step 3: Weekly HTML report

```bash
.venv/bin/python report.py                 # latest collected week
.venv/bin/python report.py --week 2026-W42
```

Writes `reports/weekly_report_<week>.html`: overview with normal-churn context, new/gone per
domain, Berlin English vs German, auto-flagged tidbits, anchored Trends charts and caveats.
Generated automatically by `run_weekly.sh` after the Sunday collection.

### Step 4: Analyze

Single city:
```bash
python3 analyze.py --city berlin
python3 analyze.py --city new_york --weeks 6
python3 analyze.py --city paris
```

Cross-city comparison (requires data for 2+ cities):
```bash
python3 analyze.py --compare
```

Outputs to `reports/`:
- `trend_scores_<city>_YYYY-WXX.csv` — scored and ranked queries
- `weekly_report_<city>_YYYY-WXX.txt` — report + LinkedIn post draft
- `comparison_report_YYYY-WXX.txt` — cross-city comparison

---

## Repositories and data backup

- **This repo (public)** holds the code only. `data/` and `reports/` are git-ignored.
- **`city-pulse-data` (private)** holds `data/` and `reports/`. It is tracked from this same
  folder through a separate git directory, so no paths change:

  ```bash
  git --git-dir=.git-data --work-tree=. status -- data reports
  ```

  `run_weekly.sh` commits and pushes the new week there after each Sunday run.
  Autocomplete history can't be re-collected later, so this backup matters.

To restore data on a new machine, clone the private repo and copy its `data/` and `reports/`
into this folder.

---

## Methodology notes

- **Google Trends** data is normalized 0–100 within each batch, so every batch
  (4 seeds) also includes the same anchor term (`TRENDS_ANCHOR` in `config.py`,
  currently "Duolingo"). `analyze.py` scores `relative_interest` = interest as a %
  of the anchor's interest in that batch and week, and `--compare` normalizes all
  cities together. Regions are state-level in both cities (DE-BE, US-NY), weekly
  over 12 months. Caveat: this makes cities comparable *relative to the anchor's
  local popularity*, not in absolute search volume, which Trends never exposes.
  Only `google_trends_anchored` rows are analyzed; older rows stay in the file.
- **Seed overrides**: short templates that get hijacked in one city (e.g.
  "founders New York" → NY Giants / NY Times) are swapped per city via
  `SEED_OVERRIDES` in `config.py`, without changing other cities' seeds.
- **Language coverage**: Berlin and Paris seeds include both English and local
  language queries (German/French) to capture full local search reality.
  New York is English only. Cross-city comparisons use the full seed set per city.
- **Autocomplete** runs plain queries only — no prefix engineering. This captures
  natural search completions rather than forcing question formats.
- **Autocomplete languages**: each seed is fetched with English results, and again with
  local-language results (`hl=de` for Berlin, `hl=fr` for Paris). The `hl` column records which.
- **Dry runs** (`--test`) write only `_dryrun` files and never touch the cumulative files.
  `analyze.py` also ignores any non-`google_trends` rows in `trends_all.csv`.
- **Google Trends** is rate-limited (HTTP 429); the collector backs off and retries.
- **Schedule**: `run_weekly.sh` runs Berlin + New York every Sunday at 19:00 via
  `~/Library/LaunchAgents/org.citypulse.weekly.plist`. Logs go to `logs/`. If the Mac is
  asleep it runs on wake; if it's shut down, that week is skipped.

---

## Roadmap

- [x] Multi-city architecture (Berlin, New York, Paris)
- [x] Local language seeds (German for Berlin, French for Paris)
- [x] Cross-city comparison report
- [ ] Week 3–4: Sentence-transformer embeddings to cluster similar queries
- [ ] Month 2: Binary trend prediction model (will this topic peak in 2 weeks?)
- [ ] Month 3: LLM layer — ask Claude to predict follow-on queries, validate against data
