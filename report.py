"""
City Cultural Pulse — weekly HTML report
Builds a single self-contained HTML page comparing Berlin and New York:
overview + normal churn, new/gone per domain, Berlin English vs German,
auto-flagged tidbits, anchored Trends charts, Google's rising searches, AI &
builder terms, Luma events (topics, venue types, areas), and data caveats.

Usage:
    python3 report.py                 # latest collected week
    python3 report.py --week 2026-W42

Output: reports/weekly_report_<week>.html (runs after the Sunday collection).
"""

import argparse
import html
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from analyze import load_trends
from config import CITIES, SEED_OVERRIDES, RISING_CATEGORIES, TECH_TOPICS, EVENT_TOPICS, HIDDEN_VENUE, UNKNOWN_VENUE

REPORTS_DIR = Path("reports")
CITY_KEYS = ["berlin", "new_york"]
DOMAINS = ["tech", "culture", "fashion", "food", "travel"]  # the original domains, compared since June
BUILDER = "builder"  # AI / builder terms, from 2026-W45, reported in their own section
SERIES = [("berlin", "Berlin", "c-berlin"), ("new_york", "New York", "c-ny")]
JUNE = ["2026-W23", "2026-W24", "2026-W25"]
LANG_NAMES = {"en": "English", "de": "German"}

SEASONAL = [
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
    "januar", "februar", "märz", "mai", "juni", "juli", "oktober", "dezember",
    "spring", "summer", "autumn", "fall", "winter", "frühling", "sommer", "herbst",
    "christmas", "xmas", "weihnachten", "weihnachtsmarkt", "weihnachtsmärkte",
    "halloween", "thanksgiving", "easter", "ostern", "silvester", "nye",
    "oktoberfest", "advent", "valentine", "valentines", "pride", "csd",
]
# Tokens that signal a search term has been hijacked by an unrelated topic
NOISE_TERMS = [
    "new york times", "new yorker", "new york post", "giants", "knicks", "yankees",
    "mets", "jets", "rangers", "liberty", "red bull", "pizza", "cheesecake", "crust",
    "dominos", "litigation", "law firm", "berlin station", "kitchen mat", "bedding",
]
CITY_WORDS = {"berlin", "berliner", "york", "nyc", "manhattan", "brooklyn"}
FOOD_ONLY_NOISE = {"pizza", "cheesecake", "crust", "dominos"}  # noise only outside food terms
STOPWORDS = set("""
a an and the of in on at to for from with by near me my is are best top how what may
where when which who why vs or new york nyc city berlin berliner paris in der die
das und mit von im am zum zur für ein eine den dem des auf nach ab bei
""".split())


# ── Helpers ───────────────────────────────────────────────────────────────────

def esc(s) -> str:
    return html.escape(str(s), quote=True)


def week_date(week: str) -> datetime:
    # Read as ISO weeks (current labels, see collect.current_week); older %W labels
    # shift by the same amount within 2026, so gaps between them stay right.
    return datetime.strptime(week + "-1", "%G-W%V-%u")


def weeks_between(a: str, b: str) -> int:
    return round((week_date(b) - week_date(a)).days / 7)


def tokens(s: str) -> set:
    return set(re.findall(r"[a-zà-ÿß0-9]+", s.lower()))


def pct(n, d) -> str:
    return f"{100 * n / d:.0f}%" if d else "–"


def novelty_rank(items: list, known_tokens: set) -> list:
    """Short suggestions that introduce at least one unseen word come first."""
    return sorted(items, key=lambda s: (not (tokens(s) - known_tokens - STOPWORDS), len(s)))


def readable_first(items: list) -> list:
    """Suggestions that mention a city come first (filters out stray completions), then shortest."""
    return sorted(items, key=lambda s: (not (tokens(s) & CITY_WORDS), len(s)))


# ── Data ──────────────────────────────────────────────────────────────────────

def load_autocomplete(city_key: str) -> pd.DataFrame:
    df = pd.read_csv(Path("data") / city_key / "autocomplete_all.csv")
    df = df[df["source"] != "autocomplete_dry_run"].copy()
    df["hl"] = df["hl"].fillna("en")
    df["s"] = df["suggestion"].astype(str).str.lower().str.strip()
    return df


class CityWeeks:
    """Per-city view of suggestion sets by week and language."""

    def __init__(self, city_key: str, week: str):
        self.key = city_key
        self.name = CITIES[city_key]["display_name"]
        df = load_autocomplete(city_key)
        self.df = df[df["domain"].isin(DOMAINS)]
        self.weeks = sorted(w for w in self.df["week"].unique() if w <= week)
        self.week = week
        self.prev = self.weeks[-2] if len(self.weeks) > 1 else None

    def rows(self, week, hl="en"):
        return self.df[(self.df["week"] == week) & (self.df["hl"] == hl)]

    def set(self, week, hl="en") -> set:
        return set(self.rows(week, hl)["s"])

    def langs(self, week) -> list:
        return sorted(self.df[self.df["week"] == week]["hl"].unique(), key=lambda h: h != "en")

    def changed_seeds(self, a, b, hl="en") -> tuple:
        sa, sb = set(self.rows(a, hl)["seed"]), set(self.rows(b, hl)["seed"])
        return sa - sb, sb - sa  # removed, added

    def normal_churn(self, hl="en") -> list:
        """% of suggestions new vs the previous week, for consecutive-week pairs only."""
        ws = sorted(self.df[self.df["hl"] == hl]["week"].unique())
        out = []
        for a, b in zip(ws, ws[1:]):
            if b <= self.week and weeks_between(a, b) == 1:
                cur, prev = self.set(b, hl), self.set(a, hl)
                if cur and prev:
                    out.append(100 * len(cur - prev) / len(cur))
        return out

    def domain_of(self, week, hl="en") -> dict:
        r = self.rows(week, hl)
        return dict(zip(r["s"], r["domain"]))

    def seed_of(self, week, hl="en") -> dict:
        r = self.rows(week, hl)
        return dict(zip(r["s"], r["seed"]))


def new_and_gone(c: CityWeeks, base_weeks: list, gone_mode: str, hl="en") -> dict:
    """
    New = this week, not in any base week. Gone = in the base ('any' base week,
    or 'all' of them = stable) but not this week. Suggestions from search terms
    that were added/removed between the periods are left out (seed changes, not
    search behaviour) and counted separately.
    """
    cur = c.set(c.week, hl)
    base_sets = [c.set(w, hl) for w in base_weeks]
    base_any = set().union(*base_sets) if base_sets else set()
    base_ref = set.intersection(*base_sets) if (gone_mode == "all" and base_sets) else base_any

    cur_seed = c.seed_of(c.week, hl)
    base_seed = {}
    for w in base_weeks:
        base_seed.update(c.seed_of(w, hl))
    cur_seeds, base_seeds = set(cur_seed.values()), set(base_seed.values())

    new = {s for s in cur - base_any if cur_seed.get(s) in base_seeds}
    gone = {s for s in base_ref - cur if base_seed.get(s) in cur_seeds}
    seed_effect = len((cur - base_any) - new) + len((base_ref - cur) - gone)

    dom_cur = c.domain_of(c.week, hl)
    dom_base = {}
    for w in base_weeks:
        dom_base.update(c.domain_of(w, hl))
    known = set().union(*(tokens(s) for s in base_any)) if base_any else set()

    by_domain = {}
    for d in DOMAINS:
        n = [s for s in new if dom_cur.get(s) == d]
        g = [s for s in gone if dom_base.get(s) == d]
        cur_known = set().union(*(tokens(s) for s in cur)) if cur else set()
        by_domain[d] = {
            "new": novelty_rank(n, known),
            "gone": novelty_rank(g, cur_known),
        }
    return {"new": new, "gone": gone, "by_domain": by_domain, "seed_effect": seed_effect,
            "cur": cur, "base": base_any}


# ── SVG charts ────────────────────────────────────────────────────────────────

def churn_bullets(rows: list) -> str:
    """
    rows: [{label, value, lo, hi, cls, tip}] — bar = this week's % new,
    grey strip = normal weekly range. Scale 0–max.
    """
    W, LW, RH, BH = 640, 170, 46, 14
    top = max([r["value"] for r in rows] + [r["hi"] or 0 for r in rows] + [10])
    top = ((int(top) // 10) + 1) * 10
    x = lambda v: LW + (W - LW - 60) * v / top
    h = len(rows) * RH + 30
    out = [f'<svg viewBox="0 0 {W} {h}" class="chart" role="img" aria-label="Share of new suggestions vs normal weekly churn">']
    for t in range(0, top + 1, 10 if top <= 60 else 20):
        out.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="0" y2="{h-26}" class="grid"/>'
                   f'<text x="{x(t):.1f}" y="{h-10}" class="tick" text-anchor="middle">{t}%</text>')
    for i, r in enumerate(rows):
        y = i * RH + 8
        out.append(f'<text x="0" y="{y + BH - 2}" class="label">{esc(r["label"])}</text>')
        if r["lo"] is not None:
            out.append(f'<rect x="{x(r["lo"]):.1f}" y="{y + BH + 3}" width="{max(x(r["hi"]) - x(r["lo"]), 2):.1f}" '
                       f'height="6" class="band" rx="3"/>'
                       f'<text x="{x(r["hi"]) + 6:.1f}" y="{y + BH + 9}" class="bandlbl">normal weekly {r["lo"]:.0f}–{r["hi"]:.0f}%</text>')
        w = max(x(r["value"]) - LW, 2)
        out.append(f'<path d="M{LW},{y} h{w-4:.1f} a4,4 0 0 1 4,4 v{BH-8} a4,4 0 0 1 -4,4 h-{w-4:.1f} z" '
                   f'class="{r["cls"]} hit" data-tip="{esc(r["tip"])}" tabindex="0"/>')
        out.append(f'<text x="{LW + w + 6:.1f}" y="{y + BH - 2}" class="value">{r["value"]:.0f}%</text>')
    out.append("</svg>")
    return "".join(out)


def mirror_bars(city: CityWeeks, by_domain: dict, cls: str) -> str:
    """Per domain: gone count to the left of the baseline, new count to the right."""
    W, LW, RH, BH = 640, 80, 30, 14
    mx = max([len(v["new"]) for v in by_domain.values()] + [len(v["gone"]) for v in by_domain.values()] + [1])
    half = (W - LW - 80) / 2
    mid = LW + 40 + half
    sx = lambda v: half * v / mx
    h = len(DOMAINS) * RH + 30
    out = [f'<svg viewBox="0 0 {W} {h}" class="chart" role="img" aria-label="{esc(city.name)}: gone and new suggestions per domain">',
           f'<text x="{mid - 8}" y="12" class="tick" text-anchor="end">← gone</text>',
           f'<text x="{mid + 8}" y="12" class="tick">new →</text>']
    for i, d in enumerate(DOMAINS):
        y = i * RH + 22
        n, g = len(by_domain[d]["new"]), len(by_domain[d]["gone"])
        out.append(f'<text x="0" y="{y + BH - 2}" class="label">{d}</text>')
        if g:
            w = max(sx(g), 4)
            out.append(f'<path d="M{mid - 1},{y} h-{w-4:.1f} a4,4 0 0 0 -4,4 v{BH-8} a4,4 0 0 0 4,4 h{w-4:.1f} z" '
                       f'class="{cls} gone hit" data-tip="{esc(f"{d}: {g} gone")}" tabindex="0"/>')
            out.append(f'<text x="{mid - 1 - w - 6:.1f}" y="{y + BH - 2}" class="value" text-anchor="end">{g}</text>')
        if n:
            w = max(sx(n), 4)
            out.append(f'<path d="M{mid + 1},{y} h{w-4:.1f} a4,4 0 0 1 4,4 v{BH-8} a4,4 0 0 1 -4,4 h-{w-4:.1f} z" '
                       f'class="{cls} hit" data-tip="{esc(f"{d}: {n} new")}" tabindex="0"/>')
            out.append(f'<text x="{mid + 1 + w + 6:.1f}" y="{y + BH - 2}" class="value">{n}</text>')
    out.append(f'<line x1="{mid}" x2="{mid}" y1="16" y2="{h - 6}" class="axis"/></svg>')
    return "".join(out)


def hbars(rows: list, unit: str = "", label_w: int = 260) -> str:
    """rows: [{label, value, cls, tip}] horizontal bars from a shared zero baseline."""
    W, RH, BH = 680, 26, 14
    mx = max([r["value"] for r in rows] + [1e-9])
    sx = lambda v: (W - label_w - 70) * v / mx
    h = len(rows) * RH + 8
    out = [f'<svg viewBox="0 0 {W} {h}" class="chart" role="img">']
    for i, r in enumerate(rows):
        y = i * RH + 4
        w = max(sx(r["value"]), 4)
        out.append(f'<text x="{label_w - 10}" y="{y + BH - 2}" class="label" text-anchor="end">{esc(r["label"])}</text>')
        out.append(f'<path d="M{label_w},{y} h{w-4:.1f} a4,4 0 0 1 4,4 v{BH-8} a4,4 0 0 1 -4,4 h-{w-4:.1f} z" '
                   f'class="{r["cls"]} hit" data-tip="{esc(r["tip"])}" tabindex="0"/>')
        out.append(f'<text x="{label_w + w + 6:.1f}" y="{y + BH - 2}" class="value">{r["value"]:.0f}{unit}</text>')
    out.append(f'<line x1="{label_w}" x2="{label_w}" y1="0" y2="{h}" class="axis"/></svg>')
    return "".join(out)


def grouped_bars(groups: list, series: list) -> str:
    """groups: [{label, values: {series_key: value}}]; series: [(key, name, cls)]."""
    W, H, PB = 680, 220, 30
    mx = max([v for g in groups for v in g["values"].values()] + [1e-9])
    gw = (W - 40) / len(groups)
    bw = min(24, (gw - 30) / len(series))
    sy = lambda v: (H - PB - 20) * v / mx
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">']
    for gi, g in enumerate(groups):
        x0 = 40 + gi * gw + (gw - bw * len(series) - 2 * (len(series) - 1)) / 2
        for si, (key, name, cls) in enumerate(series):
            v = g["values"].get(key, 0)
            h = max(sy(v), 4) if v else 0
            x = x0 + si * (bw + 2)
            yb = H - PB
            if h:
                tip = esc(f"{name} · {g['label']}: {v:.1f}")
                out.append(f'<path d="M{x:.1f},{yb} v-{h-4:.1f} a4,4 0 0 1 4,-4 h{bw-8:.1f} a4,4 0 0 1 4,4 v{h-4:.1f} z" '
                           f'class="{cls} hit" data-tip="{tip}" tabindex="0"/>')
                out.append(f'<text x="{x + bw/2:.1f}" y="{yb - h - 5:.1f}" class="value small" text-anchor="middle">{v:.0f}</text>')
        out.append(f'<text x="{40 + gi * gw + gw/2:.1f}" y="{H - 10}" class="tick" text-anchor="middle">{esc(g["label"])}</text>')
    out.append(f'<line x1="36" x2="{W}" y1="{H - PB}" y2="{H - PB}" class="axis"/></svg>')
    return "".join(out)


def paired_hbars(rows: list, series: list, unit: str = "%", label_w: int = 250) -> str:
    """rows: [{label, values: {series_key: value}}] — one thin bar per series under each label."""
    W, BH, GAP = 680, 10, 3
    RH = len(series) * (BH + GAP) + 12
    mx = max([v for r in rows for v in r["values"].values()] + [1e-9])
    sx = lambda v: (W - label_w - 60) * v / mx
    h = len(rows) * RH + 6
    out = [f'<svg viewBox="0 0 {W} {h}" class="chart" role="img">']
    for i, r in enumerate(rows):
        y0 = i * RH + 4
        out.append(f'<text x="{label_w - 10}" y="{y0 + BH + 2}" class="label" text-anchor="end">{esc(r["label"])}</text>')
        for si, (key, name, cls) in enumerate(series):
            v = r["values"].get(key, 0)
            y = y0 + si * (BH + GAP)
            if v:
                w = max(sx(v), 3)
                tip = esc(f"{name} · {r['label']}: {v:.0f}{unit}")
                out.append(f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{BH}" rx="2" class="{cls} hit" '
                           f'data-tip="{tip}" tabindex="0"/>')
                out.append(f'<text x="{label_w + w + 5:.1f}" y="{y + BH - 1}" class="value small">{v:.0f}{unit}</text>')
    out.append(f'<line x1="{label_w}" x2="{label_w}" y1="0" y2="{h}" class="axis"/></svg>')
    return "".join(out)


def legend(series: list) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i class="sw {cls}"></i>{esc(name)}</span>' for _, name, cls in series) + "</div>"


def table(headers: list, rows: list, cls: str = "") -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def table_view(headers, rows) -> str:
    return f'<details class="tv"><summary>Show as table</summary>{table(headers, [[esc(c) for c in r] for r in rows])}</details>'


def chips(items: list, n: int = 6) -> str:
    if not items:
        return '<span class="muted">—</span>'
    return "".join(f'<span class="chip">{esc(s)}</span>' for s in items[:n])


# ── Sections ──────────────────────────────────────────────────────────────────

def section_overview(cities: dict, caveats: list, highlights: list) -> str:
    tiles, bullet_rows, tv_rows = [], [], []
    beyond, within = [], []
    for c in cities.values():
        for hl in c.langs(c.week):
            cur = c.set(c.week, hl)
            prev = c.set(c.prev, hl) if c.prev else set()
            june = set().union(*(c.set(w, hl) for w in JUNE))
            churn = c.normal_churn(hl)
            new_prev = len(cur - prev) if prev else None
            new_june = len(cur - june) if june else None
            label = f"{c.name} · {LANG_NAMES.get(hl, hl)}"
            gap = weeks_between(c.prev, c.week) if c.prev else 0
            prev_note = (f"vs {c.prev}" + (f" ({gap} weeks earlier)" if gap > 1 else "")) if c.prev else ""
            lines = []
            if new_prev is not None and prev:
                lines.append(f"<b>{pct(new_prev, len(cur))}</b> new {esc(prev_note)}")
            else:
                lines.append("first week collected in this language")
            if new_june is not None and june:
                lines.append(f"<b>{pct(new_june, len(cur))}</b> new vs June (W23–W25)")
            if churn:
                lines.append(f"normal weekly churn {min(churn):.0f}–{max(churn):.0f}%")
            tiles.append(f'<div class="tile"><div class="tl">{esc(label)}</div>'
                         f'<div class="tv-big">{len(cur):,}</div><div class="ts">suggestions</div>'
                         f'<div class="tn">{"<br>".join(lines)}</div></div>')
            if new_prev is not None and prev:
                v = 100 * new_prev / len(cur)
                lo, hi = (min(churn), max(churn)) if churn else (None, None)
                cls = "c-berlin" if c.key == "berlin" else "c-ny"
                tip = f"{label}: {v:.0f}% new {prev_note}" + (f" · normal weekly {lo:.0f}–{hi:.0f}%" if churn else "")
                bullet_rows.append({"label": label, "value": v, "lo": lo, "hi": hi, "cls": cls, "tip": tip})
                tv_rows.append([label, f"{v:.0f}%", f"{lo:.0f}–{hi:.0f}%" if churn else "–", prev_note])
                if churn and v > hi + 2:
                    beyond.append(f"{esc(label)}: <b>{v:.0f}%</b> new suggestions, above the normal {lo:.0f}–{hi:.0f}%")
                elif churn:
                    within.append(label)
            if gap > 1 and hl == "en":
                caveats.append(f"{c.name}: the previous collection was {c.prev}, {gap} weeks before {c.week}. "
                               f"\"vs last week\" here spans {gap} weeks, so expect more change than the normal weekly churn.")

    for b in beyond:
        highlights.append(("Search suggestions", b + ". Something shifted; see New &amp; gone."))
    if within and not beyond:
        highlights.append(("Search suggestions", "All series changed within normal weekly churn: no broad shift in what people search."))
    churn_all = [x for c in cities.values() for x in c.normal_churn("en")]
    note = ("Google reshuffles autocomplete constantly. In weeks collected back to back, "
            f"{min(churn_all):.0f}–{max(churn_all):.0f}% of suggestions are new each week with no real change in "
            "interest. Treat changes inside the grey strip as noise; look at changes well beyond it, "
            "and at patterns that repeat across domains or cities.") if churn_all else ""
    chart = ""
    if bullet_rows:
        chart = (f'<h3>New suggestions vs last collection, against normal weekly churn</h3>'
                 f'<p class="sub">Bar = share of this week\'s suggestions not seen last time. Grey strip underneath = normal range in back-to-back weeks.</p>'
                 f'{churn_bullets(bullet_rows)}'
                 f'{table_view(["Series", "New vs last", "Normal weekly", "Compared with"], tv_rows)}')
    return (f'<section id="overview"><h2>Overview</h2><div class="tiles">{"".join(tiles)}</div>'
            f'<p class="note">{note}</p>{chart}</section>')


def section_new_gone(cities: dict, caveats: list) -> str:
    out = ['<section id="new-gone"><h2>New and gone</h2>']
    for c in cities.values():
        cls = "c-berlin" if c.key == "berlin" else "c-ny"
        if not c.prev:
            continue
        ng = new_and_gone(c, [c.prev], "any")
        ngj = new_and_gone(c, JUNE, "all") if c.week not in JUNE else None
        out.append(f'<h3><i class="sw {cls}"></i>{esc(c.name)} — English results, {esc(c.week)} vs {esc(c.prev)}</h3>')
        out.append(mirror_bars(c, ng["by_domain"], cls))
        out.append(table_view(["Domain", "New", "Gone"],
                              [[d, len(ng["by_domain"][d]["new"]), len(ng["by_domain"][d]["gone"])] for d in DOMAINS]))
        rows = []
        for d in DOMAINS:
            b = ng["by_domain"][d]
            row = [f"<b>{d}</b>", chips(b["new"]), chips(b["gone"])]
            if ngj:
                row.append(f'{len(ngj["by_domain"][d]["new"])} new · {len(ngj["by_domain"][d]["gone"])} gone')
            rows.append(row)
        hdr = ["Domain", f"New (examples, most novel first)", "Gone (examples)"] + (["vs June baseline"] if ngj else [])
        out.append(table(hdr, rows, "examples"))
        if ngj:
            out.append(f'<p class="sub">vs June: {len(ngj["new"])} suggestions never seen in June; '
                       f'{len(ngj["gone"])} that were present in all three June weeks are gone.</p>')
        if ng["seed_effect"]:
            removed, added = c.changed_seeds(c.prev, c.week)
            caveats.append(f"{c.name}: {ng['seed_effect']} new/gone suggestions came from search terms present in only one "
                           f"of {c.prev} and {c.week} ({len(added)} only now, {len(removed)} only then: changed terms, or terms "
                           "that returned nothing that week) and are left out of the counts.")
    out.append("</section>")
    return "".join(out)


def section_berlin_languages(cities: dict) -> str:
    c = cities.get("berlin")
    if not c or "de" not in c.langs(c.week):
        return ""
    en, de = c.rows(c.week, "en"), c.rows(c.week, "de")
    en_s, de_s = set(en["s"]), set(de["s"])
    dom = {**dict(zip(en["s"], en["domain"])), **dict(zip(de["s"], de["domain"]))}
    rows, tv = [], []
    for d in DOMAINS:
        only_de = readable_first([s for s in de_s - en_s if dom.get(s) == d])
        only_en = readable_first([s for s in en_s - de_s if dom.get(s) == d])
        both = len([s for s in en_s & de_s if dom.get(s) == d])
        rows.append([f"<b>{d}</b>", f"{len(only_de)}<br>{chips(only_de, 5)}",
                     f"{len(only_en)}<br>{chips(only_en, 5)}", str(both)])
        tv.append([d, len(only_de), len(only_en), both])
    total = len(en_s | de_s)
    return (f'<section id="languages"><h2>Berlin: English vs German results</h2>'
            f'<p class="sub">Same search terms, fetched with English and with German result language. '
            f'{len(de_s - en_s)} suggestions ({pct(len(de_s - en_s), total)} of all Berlin suggestions this week) appear only in German, '
            f'{len(en_s - de_s)} only in English, {len(en_s & de_s)} in both.</p>'
            f'{table(["Domain", "Only in German", "Only in English", "In both"], rows, "examples")}</section>')


def section_tidbits(cities: dict, highlights: list) -> str:
    items = []
    cs = list(cities.values())

    def count_with(sset, word):
        return sum(1 for s in sset if word in tokens(s) or (" " in word and word in s))

    # Seasonal words
    seas_rows = []
    for w in SEASONAL:
        cells, flag = [], False
        for c in cs:
            cur = sum(count_with(c.set(c.week, hl), w) for hl in c.langs(c.week))
            prev = sum(count_with(c.set(c.prev, hl), w) for hl in c.langs(c.week)) if c.prev else 0
            june = sum(count_with(c.set(wk, "en"), w) for wk in JUNE) / len(JUNE)
            cells.append(f"{june:.0f} → {prev} → <b>{cur}</b>")
            flag |= cur >= 2 and cur > max(prev, june) + 1
        if any("<b>0</b>" not in x for x in cells) or flag:
            seas_rows.append([("★ " if flag else "") + esc(w)] + cells)
    starred = [r[0][2:] for r in seas_rows if r[0].startswith("★ ")]
    if starred:
        highlights.append(("Seasonal", "Rising seasonal words in suggestions: " + ", ".join(f"<b>{w}</b>" for w in starred) + "."))
    if seas_rows:
        prevs = " / ".join(f"{c.name} {c.prev}" for c in cs)
        items.append(f'<div class="tb"><h3>Seasonal words</h3><p class="sub">Suggestions mentioning each word: '
                     f'June average → last collection ({esc(prevs)}) → this week. ★ = rising this week.</p>'
                     f'{table(["Word"] + [c.name for c in cs], seas_rows)}</div>')

    # Years
    year_rows = []
    years = sorted({y for c in cs for s in c.set(c.week) | set().union(*(c.set(w) for w in JUNE))
                    for y in re.findall(r"\b20[2-3]\d\b", s)})
    for y in years:
        cells = []
        for c in cs:
            june = sum(count_with(c.set(wk), y) for wk in JUNE) / len(JUNE)
            cells.append(f"{june:.0f} → <b>{count_with(c.set(c.week), y)}</b>")
        year_rows.append([y] + cells)
    if year_rows:
        items.append(f'<div class="tb"><h3>Years mentioned</h3><p class="sub">English suggestions mentioning each year: June average → this week.</p>'
                     f'{table(["Year"] + [c.name for c in cs], year_rows)}</div>')

    # Stable then vanished
    stable_html = []
    for c in cs:
        before = [w for w in c.weeks if w < c.week][-3:]
        if len(before) < 3:
            continue
        ng = new_and_gone(c, before, "all")
        ex = [s for d in DOMAINS for s in ng["by_domain"][d]["gone"][:2]]
        stable_html.append(f'<p><b>{esc(c.name)}</b>: {len(ng["gone"])} suggestions present in each of '
                           f'{esc(", ".join(before))} are gone. {chips(ex, 10)}</p>')
    if stable_html:
        items.append(f'<div class="tb"><h3>Stable for weeks, then vanished</h3>{"".join(stable_html)}</div>')

    # Climbing in autocomplete (positions recorded from 2026-W45)
    climb_html = []
    for c in cs:
        if "rank" not in c.df.columns or not c.prev:
            continue
        cur, prev = c.rows(c.week).dropna(subset=["rank"]), c.rows(c.prev).dropna(subset=["rank"])
        if cur.empty or prev.empty:
            continue
        m = cur.merge(prev[["seed", "s", "rank"]], on=["seed", "s"], suffixes=("", "_prev"))
        m = m[m["rank_prev"] - m["rank"] >= 3].sort_values(["rank", "rank_prev"], ascending=[True, False])
        ex = [f'{s} (#{p:.0f} → #{r:.0f})' for s, p, r in zip(m["s"], m["rank_prev"], m["rank"])]
        climb_html.append(f'<p><b>{esc(c.name)}</b>: {len(m)} suggestions moved up 3+ places for the same search term. {chips(ex, 10)}</p>')
    if climb_html:
        items.append(f'<div class="tb"><h3>Climbing in autocomplete</h3>'
                     f'<p class="sub">Position in the dropdown (1–10) vs last collection, English results. '
                     f'A suggestion moving up is a stronger signal than one simply appearing.</p>{"".join(climb_html)}</div>')

    # Both cities at once
    if len(cs) == 2 and all(c.prev for c in cs):
        seed_words = set().union(*(tokens(s) for c in cs for s in c.df["seed"].unique()))
        def counts(c, week):
            out = {}
            for s in c.set(week):
                for t in tokens(s) - STOPWORDS - seed_words:
                    if len(t) > 2:
                        out[t] = out.get(t, 0) + 1
            return out
        cur = [counts(c, c.week) for c in cs]
        prev = [counts(c, c.prev) for c in cs]
        appear = sorted([t for t in set(cur[0]) & set(cur[1]) if prev[0].get(t, 0) == 0 and prev[1].get(t, 0) == 0],
                        key=lambda t: -(cur[0][t] + cur[1][t]))
        drop = sorted([t for t in set(prev[0]) & set(prev[1])
                       if cur[0].get(t, 0) == 0 and cur[1].get(t, 0) == 0 and prev[0][t] + prev[1][t] >= 3],
                      key=lambda t: -(prev[0][t] + prev[1][t]))
        if appear or drop:
            highlights.append(("Both cities", "Moving in both cities at once: "
                               + "; ".join(x for x in [("appeared " + ", ".join(f"<b>{esc(t)}</b>" for t in appear[:5])) if appear else "",
                                                      ("dropped " + ", ".join(f"<b>{esc(t)}</b>" for t in drop[:5])) if drop else ""] if x) + "."))
        items.append(f'<div class="tb"><h3>Moving in both cities at once</h3>'
                     f'<p><b>Appeared in both</b> (absent in both last time): {chips(appear, 15)}</p>'
                     f'<p><b>Dropped in both</b> (in both last time, now in neither): {chips(drop, 15)}</p>'
                     f'<p class="sub">Words outside the search terms themselves, English results.</p></div>')

    return f'<section id="tidbits"><h2>Tidbits</h2><div class="tbs">{"".join(items)}</div></section>'


RISING_PULLS = 4      # weekly Trends pulls a rising term is checked against
RISING_MIN_PULLS = 3  # ...and must be rising in (the newest included)


def query_key(q: str) -> str:
    """Google treats word order and case as the same search ("Berlin theater" =
    "Theater Berlin"), so those are merged."""
    return " ".join(sorted(tokens(q)))


def rising_scores(df: pd.DataFrame, recent_only: bool = False) -> dict:
    """Per (query key, domain): last 4 complete weeks vs the 8 weeks before, as %
    of the term's own level, so big and small terms (and cities) compete fairly.
    The newest week is partial and left out. Terms whose baseline is under 1
    (vs the anchor) are skipped. recent_only=True returns just the recent level
    of every term with measurable interest."""
    dates = sorted(df["date"].unique())
    if len(dates) < 14:
        return {}
    recent_d, base_d = dates[-5:-1], dates[-13:-5]
    out = {}
    for (key, domain), g in df.assign(key=df["query"].map(query_key)).groupby(["key", "domain"]):
        by_date = g.groupby("date")["relative_interest"].mean()
        recent, base = by_date.reindex(recent_d).mean(), by_date.reindex(base_d).mean()
        if recent_only:
            if recent > 0:
                out[(key, domain)] = recent
        elif not pd.isna(base) and base >= 1:
            out[(key, domain)] = {"key": key, "query": g["query"].iloc[0], "domain": domain, "recent": recent,
                                  "base": base, "change": 100 * (recent / base - 1)}
    return out


def pull_files(city_key: str, week: str) -> list:
    """(week, path) of each weekly Trends pull up to `week`, newest last, at most
    RISING_PULLS. The 2026-W42 run was re-fetched with the anchor into a separate
    _anchored file, which is preferred."""
    files = {}
    for p in sorted((Path("data") / city_key).glob("trends_20??-W??*.csv")):
        m = re.fullmatch(r"trends_(\d{4}-W\d\d)(_anchored)?\.csv", p.name)
        if m and m.group(1) <= week and (m.group(2) or m.group(1) not in files):
            files[m.group(1)] = p
    return sorted(files.items())


def trends_pulls(city_key: str, week: str) -> list:
    pulls = []
    for _, p in pull_files(city_key, week):
        df = load_trends(p) if p.stat().st_size > 1 else pd.DataFrame()
        if not df.empty:
            pulls.append(df[df["domain"].isin(DOMAINS)])
    return pulls[-RISING_PULLS:]


def pulls_label(city_key: str, week: str) -> str:
    weeks = [w for w, p in pull_files(city_key, week)
             if p.stat().st_size > 1 and not load_trends(p).empty]
    return ", ".join(weeks[-RISING_PULLS:])


def section_trends(week: str, caveats: list, highlights: list) -> str:
    loaded = {}
    for k in CITY_KEYS:
        p = Path("data") / k / "trends_all.csv"
        if p.exists():
            df = load_trends(p)
            df = df[df["domain"].isin(DOMAINS)] if not df.empty else df
            if not df.empty:
                loaded[k] = df
    anchors = {a for df in loaded.values() for a in df["anchor"].unique()}
    comparable = len(loaded) == len(CITY_KEYS) and len(anchors) == 1

    head = '<section id="trends"><h2>Google Trends</h2>'
    if not loaded:
        return head + '<p class="badge warn">No anchored Trends data yet — nothing to chart.</p></section>'

    # Coverage / empty terms
    for k, df in loaded.items():
        last12 = df.groupby("query")["interest_score"].sum()
        zero = (last12 == 0).sum()
        if zero:
            caveats.append(f"{CITIES[k]['display_name']} Trends: {zero} of {len(last12)} search terms had zero measurable "
                           f"interest over 12 months (too little search volume in the region) and can't move the scores. "
                           f"From 2026-W45 they're skipped in Trends (still collected for autocomplete) and re-checked every 4th week.")
    caveats.append("Trends: the most recent week is partial (Google marks it as incomplete), so its values can shift next run.")

    if not comparable:
        return (head + '<p class="badge warn">Not comparable: Berlin and New York aren\'t both on the same anchor yet, '
                'so their scores are on different scales. Charts are withheld until they are.</p></section>')

    anchor = anchors.pop()
    series = [("berlin", "Berlin", "c-berlin"), ("new_york", "New York", "c-ny")]
    cls = {"berlin": "c-berlin", "new_york": "c-ny"}

    # Domain averages come from the cumulative file (newest values per week).
    groups_raw = {d: {} for d in DOMAINS}
    for k, df in loaded.items():
        for (key, domain), recent in rising_scores(df, recent_only=True).items():
            groups_raw[domain].setdefault(k, []).append(recent)

    # Rising terms must hold up across weekly pulls. Trends re-sends all 12 months
    # every run and its values for the same past week shift between pulls (in
    # Berlin by 10% typically, 200%+ for small terms that flip between 0 and a
    # few points), so a single pull can show +500% from a baseline that was 0 the
    # week before. A term counts as rising only if it rises in at least
    # RISING_MIN_PULLS of the last RISING_PULLS pulls, including the newest, and
    # it's ranked by its median change across those pulls.
    rising = []
    for k in loaded:
        pulls = trends_pulls(k, week)
        if len(pulls) < RISING_MIN_PULLS:
            continue
        scores = [rising_scores(p) for p in pulls]
        latest = scores[-1]
        for key, r in latest.items():
            changes = [s[key]["change"] for s in scores if key in s]
            up = [c for c in changes if c > 0]
            if r["change"] <= 0 or len(up) < RISING_MIN_PULLS:
                continue
            rising.append({**r, "city": k, "change": float(pd.Series(changes).median()),
                           "lo": min(changes), "hi": max(changes), "up": len(up), "pulls": len(scores)})
        caveats.append(f"{CITIES[k]['display_name']} Trends: rising terms checked across {len(scores)} weekly pulls "
                       f"({pulls_label(k, week)}).")
    if not rising:
        return head + (f'<p class="badge warn">No term rose consistently (needs {RISING_MIN_PULLS} of the last '
                       f'{RISING_PULLS} weekly pulls and 13 weeks of anchored data).</p></section>')

    for k in loaded:
        mine = sorted([r for r in rising if r["city"] == k], key=lambda r: -r["change"])
        name = CITIES[k]["display_name"]
        if mine:
            terms = ", ".join(f'<b>{esc(r["query"])}</b> +{r["change"]:.0f}%' for r in mine[:3])
            more = f" and {len(mine) - 3} more" if len(mine) > 3 else ""
            highlights.append(("Google Trends", f"{name}: consistently rising: {terms}{more} "
                               f"(median change across the last {mine[0]['pulls']} weekly pulls)."))
        elif trends_pulls(k, week):
            highlights.append(("Google Trends", f"{name}: no search term rose consistently across the last weekly pulls."))
    rdf = pd.DataFrame(rising).drop_duplicates(["city", "key"])  # a term used in two domains counts once
    top = (rdf.sort_values("change", ascending=False)
           .groupby("city", sort=False).head(6).sort_values("change", ascending=False))
    counts = "; ".join(f'{CITIES[k]["display_name"]}: {(rdf["city"] == k).sum()} rising consistently'
                       for k in loaded)
    top_rows = [{"label": f'{r["query"]} ({r["domain"]})', "value": r["change"], "cls": cls[r["city"]],
                 "tip": f'{CITIES[r["city"]]["display_name"]} · {r["query"]}: +{r["change"]:.0f}% median, '
                        f'{r["lo"]:+.0f}% to {r["hi"]:+.0f}% across pulls '
                        f'(now {r["base"]:.1f} → {r["recent"]:.1f} vs {anchor})'} for _, r in top.iterrows()]

    groups = [{"label": d, "values": {k: sum(v) / len(v) for k, v in groups_raw[d].items()}} for d in DOMAINS]

    return (head + f'<p class="badge ok">Comparable — every batch in both cities includes the anchor term "{esc(anchor)}", '
            'and all values are interest relative to it.</p>'
            '<p class="sub">This compares each city relative to how much people there search for the anchor term, '
            'not absolute search volume (which Trends never reveals).</p>'
            f'<h3>Fastest-rising search terms (top 6 per city)</h3>'
            f'<p class="sub">Last 4 complete weeks vs the 8 weeks before, as % change. Trends revises past weeks every '
            f'time it\'s fetched, so only terms rising in at least {RISING_MIN_PULLS} of the last {RISING_PULLS} weekly '
            f'pulls are shown, ranked by their median change. {esc(counts)}.</p>'
            f'{legend(series)}{hbars(top_rows, unit="%")}'
            + table_view(["City", "Term", "Domain", "Median change", "Range across pulls", "Rising in", "Before → now (vs anchor)"],
                         [[CITIES[r["city"]]["display_name"], r["query"], r["domain"], f'+{r["change"]:.0f}%',
                           f'{r["lo"]:+.0f}% to {r["hi"]:+.0f}%', f'{r["up"]} of {r["pulls"]} pulls',
                           f'{r["base"]:.1f} → {r["recent"]:.1f}'] for _, r in top.iterrows()])
            + f'<h3>Average interest per domain, relative to "{esc(anchor)}" (last 4 complete weeks)</h3>'
            f'<p class="sub">100 = searched as much as the anchor term in that city. Averages terms with measurable interest.</p>'
            f'{legend(series)}{grouped_bars(groups, series)}'
            + table_view(["Domain", "Berlin", "New York"],
                         [[g["label"], f'{g["values"].get("berlin", 0):.1f}', f'{g["values"].get("new_york", 0):.1f}'] for g in groups])
            + "</section>")


def section_builder(week: str) -> str:
    """AI / builder terms: Trends level and change per city, plus what autocomplete attaches to them."""
    head = ('<section id="builder"><h2>AI &amp; builder terms</h2>'
            '<p class="sub">Tech and AI concepts people building things search for, without the city name. '
            'Trends is limited to each region (Berlin state, New York state); autocomplete only works per country (Germany, US).</p>')
    body = []

    # Trends: last 4 complete weeks vs the 8 before, relative to the anchor
    rows = {}
    for k, name, _ in SERIES:
        p = Path("data") / k / "trends_all.csv"
        df = load_trends(p) if p.exists() else pd.DataFrame()
        df = df[df["domain"] == BUILDER] if not df.empty else df
        if df.empty:
            continue
        dates = sorted(df["date"].unique())
        if len(dates) < 14:
            continue
        recent_d, base_d = dates[-5:-1], dates[-13:-5]
        for q, g in df.groupby("query"):
            by_date = g.groupby("date")["relative_interest"].mean()
            recent, base = by_date.reindex(recent_d).mean(), by_date.reindex(base_d).mean()
            rows.setdefault(q, {})[k] = (recent, 100 * (recent / base - 1) if base and base >= 1 else None)
    if rows:
        order = sorted(rows, key=lambda q: -max(v[0] for v in rows[q].values()))
        bars = [{"label": q, "values": {k: rows[q][k][0] for k in rows[q] if pd.notna(rows[q][k][0])}} for q in order]
        fmt_c = lambda c: "–" if c is None or pd.isna(c) else f"{c:+.0f}%"
        tv = [[q] + [f"{rows[q][k][0]:.1f}" if k in rows[q] else "–" for k, _, _ in SERIES]
                  + [fmt_c(rows[q][k][1]) if k in rows[q] else "–" for k, _, _ in SERIES] for q in order]
        body.append('<h3>Search interest per term, relative to the anchor (last 4 complete weeks)</h3>'
                    '<p class="sub">100 = searched as much as the anchor term in that region. Change = vs the 8 weeks before.</p>'
                    + legend(SERIES) + paired_hbars(bars, SERIES, unit="")
                    + table(["Term", "Berlin", "New York", "Berlin change", "New York change"], [[esc(c) for c in r] for r in tv]))

    # Autocomplete: what people attach to each term this week
    ac_rows = []
    for k, name, _ in SERIES:
        df = load_autocomplete(k)
        df = df[(df["domain"] == BUILDER) & (df["hl"] == "en")]
        if df.empty or week not in set(df["week"]):
            continue
        earlier = sorted(w for w in df["week"].unique() if w < week)
        prev = df[df["week"] == earlier[-1]] if earlier else df.iloc[0:0]
        cur = df[df["week"] == week]
        for seed, g in cur.groupby("seed", sort=False):
            seen = set(prev[prev["seed"] == seed]["s"])
            items = [("★ " if seen and s not in seen else "") + s for s in g.sort_values("rank")["s"]] if "rank" in g else list(g["s"])
            ac_rows.append([esc("Germany" if k == "berlin" else "US"), esc(seed), chips(items, 10)])
    if ac_rows:
        body.append('<h3>What autocomplete attaches to each term</h3>'
                    '<p class="sub">English results, in dropdown order. ★ = not suggested for this term last week.</p>'
                    + table(["Country", "Term", "Suggestions"], ac_rows, "examples"))

    if not body:
        return head + '<p class="badge warn">No AI &amp; builder data for this week (collected from 2026-W45).</p></section>'
    return head + "".join(body) + "</section>"


def load_events(city_key: str) -> pd.DataFrame:
    p = Path("data") / city_key / "events_all.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["topics"] = df["topics"].fillna("")
    for col in ["venue", "area", "organizer", "location", "venue_hint", "described_type"]:
        df[col] = df[col].fillna("") if col in df else ""
    return df


def shares(df: pd.DataFrame, col: str) -> dict:
    return (100 * df[col].value_counts() / len(df)).to_dict() if len(df) else {}


def section_events(week: str, caveats: list, highlights: list) -> str:
    head = ('<section id="events"><h2>Events on Luma</h2>'
            '<p class="sub">Each city\'s Luma Discover feed (official iCal subscription), collected weekly. It is Luma\'s featured slice '
            '(about 50 upcoming events): it reaches about 4 weeks ahead in Berlin but only about a week in New York, so compare '
            'shares, not counts. Covers events that entered the feed in the last 4 collected weeks. Tech / startup = '
            'tagged with any of: ' + esc(", ".join(sorted(TECH_TOPICS))) + '.</p>')
    ev = {}
    for k, _, _ in SERIES:
        df = load_events(k)
        if df.empty:
            continue
        weeks = sorted(w for w in df["first_seen"].unique() if w <= week)[-4:]
        df = df[df["first_seen"].isin(weeks)]
        if not df.empty:
            ev[k] = (df, weeks)
    if not ev:
        return head + '<p class="badge warn">No events collected yet (from 2026-W45).</p></section>'

    name = {k: n for k, n, _ in SERIES}
    tiles, out = [], []
    for k, (df, weeks) in ev.items():
        t = df[df["tech"]]
        hidden = (t["venue_type"] == HIDDEN_VENUE).mean() * 100 if len(t) else 0
        tiles.append(f'<div class="tile"><div class="tl">{esc(name[k])}</div><div class="tv-big">{len(df)}</div>'
                     f'<div class="ts">events ({esc(weeks[0])}–{esc(weeks[-1])})</div>'
                     f'<div class="tn"><b>{pct(len(t), len(df))}</b> tech / startup<br>'
                     f'<b>{hidden:.0f}%</b> of those with the address hidden until approved</div></div>')
    out.append(f'<div class="tiles">{"".join(tiles)}</div>')

    tech = {k: df[df["tech"]] for k, (df, _) in ev.items()}
    other = {k: df[~df["tech"]] for k, (df, _) in ev.items()}

    for k, (df, weeks) in ev.items():
        t, o = tech[k], other[k]
        if len(t) < 5:
            continue
        bits = [f"{pct(len(t), len(df))} of featured events are tech / startup"]
        topic = max(EVENT_TOPICS, key=lambda tp: t["topics"].str.contains(re.escape(tp)).mean())
        bits.append(f"most often about {esc(topic)} ({100 * t['topics'].str.contains(re.escape(topic)).mean():.0f}%)")
        h_t = (t["venue_type"] == HIDDEN_VENUE).mean() * 100
        h_o = (o["venue_type"] == HIDDEN_VENUE).mean() * 100 if len(o) else 0
        if h_t - h_o >= 15:
            bits.append(f"<b>{h_t:.0f}%</b> hide the address until approved, vs {h_o:.0f}% of other events")
        span = f"{len(weeks)} week{'s' if len(weeks) > 1 else ''} of data, {len(t)} tech events"
        highlights.append(("Events", f"{name[k]}: " + "; ".join(bits) + f" ({span})."))

    # Venue types
    vt = {k: shares(tech[k], "venue_type") for k in ev}
    vo = {k: shares(other[k], "venue_type") for k in ev}
    types = sorted(set().union(*vt.values(), *vo.values()), key=lambda x: -sum(vt[k].get(x, 0) for k in ev))
    out.append('<h3>Where tech &amp; startup events happen</h3>'
               '<p class="sub">Share of tech / startup events by venue type. "Address hidden" = the host shows the venue only '
               'after approving your registration (private homes, offices, curated guest lists).</p>'
               + legend(SERIES) + paired_hbars([{"label": x, "values": {k: vt[k].get(x, 0) for k in ev}} for x in types if any(vt[k].get(x) for k in ev)], SERIES))
    hdr = ["Venue type"] + [f"{name[k]} tech" for k in ev] + [f"{name[k]} other" for k in ev]
    out.append('<p class="sub">Compared with all other events (culture, food, social…):</p>'
               + table(hdr, [[esc(x)] + [f"{vt[k].get(x, 0):.0f}%" for k in ev] + [f"{vo[k].get(x, 0):.0f}%" for k in ev] for x in types]))

    # Hidden addresses: what hosts say about the place themselves
    rows, todo = [], []
    for k in ev:
        h = tech[k][tech[k]["venue_type"] == HIDDEN_VENUE]
        if h.empty:
            continue
        typed = h[h["described_type"] != ""]
        pending = h[(h["described_type"] == "") & (h["venue_hint"] != "")]
        silent = len(h) - len(typed) - len(pending)
        rows.append([f"<b>{esc(name[k])}</b>", str(len(h)),
                     chips([f"{x} ({n})" for x, n in typed["described_type"].value_counts().items()], 8),
                     str(len(pending)), str(silent)])
        todo += [[esc(name[k]), esc(r.title[:70]), f'<span class="muted">{esc(r.venue_hint)}</span>', f"<code>{esc(r.uid)}</code>"]
                 for r in pending.itertuples()]
    if rows:
        out.append('<h3>Hidden addresses: what hosts say about the place</h3>'
                   '<p class="sub">From the host\'s own public title and description only; addresses are never looked up '
                   'elsewhere. Types are confirmed by hand in <code>data/event_venues.csv</code>.</p>'
                   + table(["City", "Hidden tech events", "Described as", "Described, type not set yet", "Not described"], rows))
    if todo:
        out.append('<details class="tv"><summary>Hidden-address events that describe their venue, awaiting a type '
                   f'({len(todo)})</summary>' + table(["City", "Event", "What the host says", "uid"], todo, "examples") + '</details>')
        caveats.append(f"Events: {len(todo)} hidden-address events describe their venue but have no type yet; add them to "
                       "data/event_venues.csv (uid, city, title, described_type), using the venue types from venues.csv.")

    # Topics
    top = {k: {tp: 100 * tech[k]["topics"].str.contains(re.escape(tp)).mean() for tp in EVENT_TOPICS} if len(tech[k]) else {} for k in ev}
    topics = sorted([tp for tp in EVENT_TOPICS if any(top[k].get(tp) for k in ev)], key=lambda tp: -sum(top[k].get(tp, 0) for k in ev))
    out.append('<h3>What tech &amp; startup events are about</h3>'
               '<p class="sub">Share of tech / startup events with each topic (an event can have several).</p>'
               + legend(SERIES) + paired_hbars([{"label": tp, "values": {k: top[k].get(tp, 0) for k in ev}} for tp in topics], SERIES))

    # Areas and time of day
    rows = []
    for k in ev:
        a_t = tech[k][tech[k]["area"] != ""]["area"].value_counts()
        a_o = other[k][other[k]["area"] != ""]["area"].value_counts()
        rows.append([f"<b>{esc(name[k])}</b>", chips([f"{a} ({n})" for a, n in a_t.items()], 8),
                     chips([f"{a} ({n})" for a, n in a_o.items()], 8)])
    out.append('<h3>Neighbourhoods</h3><p class="sub">Events with a known address. Berlin areas are approximate (from postcodes).</p>'
               + table(["City", "Tech / startup events", "Other events"], rows, "examples"))
    sl = {k: shares(tech[k], "slot") for k in ev}
    so = {k: shares(other[k], "slot") for k in ev}
    out.append('<h3>Time of day</h3>' + table(["Start"] + hdr[1:],
               [[s] + [f"{sl[k].get(s, 0):.0f}%" for k in ev] + [f"{so[k].get(s, 0):.0f}%" for k in ev]
                for s in ["Morning", "Afternoon", "Evening"]]))

    # Recurring venues and new hosts
    rows = []
    for k, (df, weeks) in ev.items():
        t = tech[k]
        place = t["venue"].where(t["venue"] != "", t["location"].str.split(",").str[0])
        place = place[~t["venue_type"].isin([HIDDEN_VENUE, "Online"])]
        rec = place.value_counts()
        full = load_events(k)
        before = set(full[full["first_seen"] < week]["organizer"])
        new_hosts = sorted(set(t[(t["first_seen"] == week) & (t["organizer"] != "")]["organizer"]) - before)
        rows.append([f"<b>{esc(name[k])}</b>", chips([f"{v} ({n})" for v, n in rec[rec >= 2].items()], 10),
                     chips(new_hosts, 12) if len(full["first_seen"].unique()) > 1 else '<span class="muted">from next week</span>'])
    out.append('<h3>Recurring venues and new hosts</h3>'
               '<p class="sub">Venues with 2+ tech / startup events in the period; hosts of tech / startup events seen for the first time this week.</p>'
               + table(["City", "Recurring venues", "New hosts this week"], rows, "examples"))

    for k, (df, _) in ev.items():
        u = df[df["venue_type"] == UNKNOWN_VENUE].assign(place=lambda d: d["location"].str.split(",").str[0].str.strip())
        u = u.sort_values("place").drop_duplicates("place")
        if len(u):
            sug = u["venue_suggestion"].fillna("") if "venue_suggestion" in u else pd.Series("", index=u.index)
            caveats.append(f"{name[k]} events: {len(u)} venues have no venue type yet (add them to venues.csv; "
                           "suggestions come from the event text and need checking): "
                           + ", ".join(f'"{p}"' + (f" (suggested: {g})" if g else "") for p, g in zip(u["place"], sug))
                           + ".")
    caveats.append("Events: topics and tech / startup tagging come from keywords in titles and descriptions, so expect a few misfiles.")
    return head + "".join(out) + "</section>"


def load_rising(city_key: str) -> pd.DataFrame:
    p = Path("data") / city_key / "rising_all.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def section_rising(week: str, caveats: list) -> str:
    head = ('<section id="rising"><h2>Google\'s rising searches</h2>'
            '<p class="sub">Per domain, the searches Google Trends reports as growing fastest over the last 3 months '
            'within a whole Trends category in each region (Berlin state, New York state). Not limited to our search terms, '
            'so expect some sports, TV and celebrity searches. % = growth; Breakout = more than +5000%. '
            '★ = not on the list last week.</p>')
    blocks = []
    for k in CITY_KEYS:
        df = load_rising(k)
        if df.empty or week not in set(df["week"]):
            continue
        earlier = sorted(w for w in df["week"].unique() if w < week)
        prev = df[df["week"] == earlier[-1]] if earlier else df.iloc[0:0]
        cur = df[df["week"] == week]
        rows = []
        for d in DOMAINS:
            g = cur[cur["domain"] == d].sort_values("rank")
            if g.empty:
                continue
            seen = set(prev[prev["domain"] == d]["query"].str.lower())
            cells = "".join(
                f'<span class="chip">{"★ " if earlier and q.lower() not in seen else ""}{esc(q)} '
                f'<span class="muted">{"Breakout" if v >= 5000 else f"+{v}%"}</span></span>'
                for q, v in zip(g["query"].head(10), g["value"].head(10)))
            rows.append([f"<b>{esc(d)}</b>", esc(g["category"].iloc[0]), cells])
        missing = [name for d, (_, name) in RISING_CATEGORIES.items() if d not in set(cur["domain"])]
        if missing:
            caveats.append(f'{CITIES[k]["display_name"]} rising searches: no data this week for '
                           + ", ".join(missing) + " (the request failed or was rate-limited).")
        blocks.append(f'<h3>{esc(CITIES[k]["display_name"])}</h3>'
                      + table(["Domain", "Trends category", "Rising searches"], rows, "examples"))
    if not blocks:
        return head + '<p class="badge warn">No rising-searches data for this week (collected from 2026-W45).</p></section>'
    return head + "".join(blocks) + "</section>"


def noise_caveats(cities: dict, caveats: list):
    for c in cities.values():
        r = c.rows(c.week)
        bad = []
        for (seed, domain), g in r.groupby(["seed", "domain"]):
            terms = [n for n in NOISE_TERMS if domain != "food" or n not in FOOD_ONLY_NOISE]
            hits = sum(1 for s in g["s"] if any(n in s for n in terms))
            if len(g) and hits / len(g) >= 0.3:
                bad.append(f"\"{seed}\" ({hits}/{len(g)})")
        if bad:
            replaced = [k.replace("{city}", c.name) for k in SEED_OVERRIDES.get(c.key, {})]
            done = [b for b in bad if any(b.startswith(f'"{r}"') for r in replaced)]
            caveats.append(f"{c.name}: search terms pulling in unrelated results this week "
                           f"(share of suggestions about sports teams, newspapers, pizza, etc.): {', '.join(bad)}."
                           + (f" {len(done)} of these are already replaced with tighter terms from the next run." if done else ""))
        thin = [s for s, n in r.groupby("seed").size().items() if n < 3]
        if thin:
            caveats.append(f"{c.name}: {len(thin)} search terms returned fewer than 3 suggestions: "
                           + ", ".join(f'"{s}"' for s in thin[:8]) + ("…" if len(thin) > 8 else "") + ".")
        overrides = SEED_OVERRIDES.get(c.key, {})
        if overrides and c.prev:
            removed, added = c.changed_seeds(c.prev, c.week)
            if added:
                caveats.append(f"{c.name}: {len(added)} search terms are new this week (tightened replacements), "
                               "so their suggestions only become comparable from next week.")
    if "2026-W23" in JUNE:
        caveats.append("June W23 combines two runs (June 10 and 11), so it holds slightly more suggestions than a normal week.")


# ── Page ──────────────────────────────────────────────────────────────────────

CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;
--line:rgba(11,11,11,.10);--berlin:#2a78d6;--ny:#eb6834;--band:#c3c2b7;--chip:#f0efec;--ok:#006300;--warn:#8a5a00;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--axis:#383835;--line:rgba(255,255,255,.10);--berlin:#3987e5;--ny:#d95926;--band:#6b6a65;--chip:#2c2c2a;
--ok:#0ca30c;--warn:#fab219;color-scheme:dark}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 20px 64px}
header h1{font-size:28px;margin:0 0 4px}header p{color:var(--ink2);margin:0}
nav{display:flex;flex-wrap:wrap;gap:6px 16px;margin:16px 0 8px;font-size:14px}nav a{color:var(--ink2)}
section{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px 24px;margin:20px 0}
h2{font-size:20px;margin:0 0 12px}h3{font-size:15px;margin:22px 0 4px;display:flex;align-items:center;gap:8px}
.sub,.note{color:var(--ink2);font-size:13.5px;margin:4px 0 10px}.note{border-left:3px solid var(--axis);padding-left:10px}
.muted{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.tile{border:1px solid var(--line);border-radius:10px;padding:14px}.tl{font-size:13px;color:var(--ink2)}
.tv-big{font-size:32px;font-weight:600;line-height:1.2}.ts{font-size:12px;color:var(--muted)}.tn{font-size:13px;margin-top:8px;color:var(--ink2)}
.tn b{color:var(--ink)}
.chart{width:100%;max-width:760px;height:auto;display:block;margin:6px 0}
.chart .grid{stroke:var(--grid);stroke-width:1}.chart .axis{stroke:var(--axis);stroke-width:1}
.chart text{fill:var(--ink2);font-size:12px}.chart .label{fill:var(--ink);font-size:12.5px}.chart .value{fill:var(--ink);font-size:12px;font-variant-numeric:tabular-nums}
.chart .small{font-size:11px}.chart .tick{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.chart .band{fill:var(--band)}.chart .bandlbl{fill:var(--muted);font-size:10.5px}
.c-berlin{fill:var(--berlin);background:var(--berlin)}.c-ny{fill:var(--ny);background:var(--ny)}
.chart .gone{opacity:.55}.hit{cursor:default;outline:none}.hit:hover,.hit:focus{opacity:.8;stroke:var(--ink);stroke-width:1}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px}
.legend{display:flex;gap:16px;font-size:13px;color:var(--ink2);margin:6px 0}.legend span{display:flex;align-items:center;gap:6px}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:8px 0}th{text-align:left;color:var(--ink2);font-weight:500;border-bottom:1px solid var(--axis);padding:6px 8px}
td{border-bottom:1px solid var(--grid);padding:7px 8px;vertical-align:top;font-variant-numeric:tabular-nums}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:1px 7px;margin:2px 4px 2px 0;font-size:12.5px}
details.tv{margin:2px 0 8px;font-size:13px;color:var(--ink2)}details.tv summary{cursor:pointer}
.tbs{display:grid;gap:8px}.tb h3{margin-top:12px}
ul.hl{list-style:none;padding:0;margin:8px 0}ul.hl li{padding:8px 0;border-top:1px solid var(--line);display:flex;gap:12px;flex-wrap:wrap}
ul.hl li:first-child{border-top:0}.hk{flex:0 0 140px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);padding-top:2px}
.badge{display:inline-block;border-radius:8px;padding:6px 10px;font-size:13.5px;border:1px solid var(--line)}
.badge.ok::before{content:"✓ ";color:var(--ok);font-weight:700}.badge.warn::before{content:"⚠ ";color:var(--warn);font-weight:700}
ul.caveats li{margin:4px 0;color:var(--ink2)}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--surface);font-size:12.5px;padding:5px 9px;border-radius:6px;max-width:320px;z-index:9}
@media (max-width:600px){section{padding:16px}main{padding:20px 16px 48px}}
"""

JS = """
const tip=document.getElementById('tip');
function show(e,el){tip.textContent=el.dataset.tip;tip.hidden=false;
 const r=el.getBoundingClientRect(),x=e&&e.clientX?e.clientX:r.right,y=e&&e.clientY?e.clientY:r.top;
 tip.style.left=Math.min(x+12,innerWidth-tip.offsetWidth-8)+'px';tip.style.top=(y-34)+'px';}
document.querySelectorAll('[data-tip]').forEach(el=>{
 el.addEventListener('pointermove',e=>show(e,el));el.addEventListener('focus',()=>show(null,el));
 el.addEventListener('pointerleave',()=>tip.hidden=true);el.addEventListener('blur',()=>tip.hidden=true);});
document.querySelectorAll('section table').forEach(t=>{const w=document.createElement('div');w.className='table-wrap';t.replaceWith(w);w.appendChild(t);});
"""


def section_summary(highlights: list, caveats: list) -> str:
    """The week at a glance: only findings that already passed each section's
    noise checks (churn range, consistency across Trends pulls), in a fixed order."""
    order = ["Google Trends", "Search suggestions", "Both cities", "Seasonal", "Events"]
    rows = sorted(highlights, key=lambda h: order.index(h[0]) if h[0] in order else len(order))
    items = "".join(f'<li><span class="hk">{esc(k)}</span>{v}</li>' for k, v in rows)
    todo = sum(1 for c in caveats if "add them to" in c or "no venue type yet" in c)
    foot = (f'<p class="sub">{todo} data to-do{"s" if todo != 1 else ""} (unclassified venues or events), see '
            f'<a href="#caveats">Caveats</a>.</p>') if todo else ""
    return (f'<section id="summary"><h2>This week</h2><p class="sub">Only findings that pass the noise checks below. '
            f'Everything else in this report is detail.</p><ul class="hl">{items}</ul>{foot}</section>')


def build(week: str) -> str:
    cities = {k: CityWeeks(k, week) for k in CITY_KEYS}
    caveats, highlights = [], []
    body = [
        section_overview(cities, caveats, highlights),
        section_new_gone(cities, caveats),
        section_berlin_languages(cities),
        section_tidbits(cities, highlights),
        section_trends(week, caveats, highlights),
        section_rising(week, caveats),
        section_builder(week),
        section_events(week, caveats, highlights),
    ]
    noise_caveats(cities, caveats)
    body.insert(0, section_summary(highlights, caveats))
    body.append('<section id="caveats"><h2>Caveats</h2><ul class="caveats">'
                + "".join(f"<li>{esc(c)}</li>" for c in caveats) + "</ul></section>")
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    nav = "".join(f'<a href="#{i}">{t}</a>' for i, t in [
        ("summary", "This week"), ("overview", "Overview"), ("new-gone", "New &amp; gone"), ("languages", "Berlin EN vs DE"),
        ("tidbits", "Tidbits"), ("trends", "Trends"), ("rising", "Rising searches"), ("builder", "AI &amp; builder terms"),
        ("events", "Events"), ("caveats", "Caveats")])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>City Pulse {esc(week)}</title><style>{CSS}</style></head><body><main>'
            f'<header><h1>City Cultural Pulse — {esc(week)}</h1>'
            f'<p>Berlin vs New York · Google autocomplete + Trends · generated {generated}</p></header>'
            f'<nav>{nav}</nav>{"".join(body)}</main><div id="tip" hidden></div><script>{JS}</script></body></html>')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", help="Week to report on, e.g. 2026-W42 (default: latest collected)")
    args = parser.parse_args()

    week = args.week or max(load_autocomplete(k)["week"].max() for k in CITY_KEYS)
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"weekly_report_{week}.html"
    path.write_text(build(week), encoding="utf-8")
    print(f"Report saved to {path}")


if __name__ == "__main__":
    main()
