"""
City Cultural Pulse — shared configuration
Defines seed queries per domain for Berlin, New York, and Paris.
Each city has English seeds plus local-language seeds where applicable.
"""

# ── City definitions ──────────────────────────────────────────────────────────

CITIES = {
    "berlin": {
        "display_name": "Berlin",
        "geo_trends": "DE-BE",  # Berlin state, to match New York's state-level US-NY
        "geo_autocomplete": "DE",
        "hl_primary": "en",
        "hl_local": "de",
        "tz": 60,
    },
    "new_york": {
        "display_name": "New York",
        "geo_trends": "US-NY",
        "geo_autocomplete": "US",
        "hl_primary": "en",
        "hl_local": None,  # English only
        "tz": -300,
    },
    "paris": {
        "display_name": "Paris",
        "geo_trends": "FR",
        "geo_autocomplete": "FR",
        "hl_primary": "en",
        "hl_local": "fr",
        "tz": 60,
    },
}

# ── Google Trends anchoring ───────────────────────────────────────────────────
# Trends rescales every request to 0–100, so scores from different batches (and
# cities) aren't comparable. Every batch includes the same anchor term, and
# analysis uses interest relative to it (100 = as searched as the anchor, locally).
# Duolingo was picked for being stable week to week in both DE-BE and US-NY,
# never zero, and close in size to the larger seeds (tested 2026-W42).
# Caveat: it spikes every January (New Year's resolutions).

TRENDS_ANCHOR = "Duolingo"
TRENDS_TIMEFRAME = "today 12-m"  # weekly resolution; fewer zero values than daily

# ── Google Trends rising searches ─────────────────────────────────────────────
# Per domain, the searches Trends reports as rising fastest in a whole Trends
# category in the city's region (all Food & Drink searches in Berlin, etc.).
# Our own seed terms are too small for this: none returned rising searches
# (tested 2026-W44). Categories mix in sports and celebrity searches, and
# Computers & Electronics is consumer tech rather than the startup scene.
# Added 2026-W45.

RISING_TIMEFRAME = "today 3-m"

RISING_CATEGORIES = {        # domain: (Trends category id, name)
    "tech":    (5, "Computers & Electronics"),
    "culture": (3, "Arts & Entertainment"),
    "fashion": (68, "Apparel"),
    "food":    (71, "Food & Drink"),
    "travel":  (67, "Travel"),
}

# ── Seed query templates ──────────────────────────────────────────────────────
# Use {city} as placeholder — replaced at runtime with the city display name.

SEED_TEMPLATES = {
    "tech": {
        "en": [
            "AI companies in {city}",
            "best tech startups in {city}",
            "{city} startup scene",
            "working in tech in {city}",
            "{city} fintech",
            "venture capital {city}",
            "AI jobs {city}",
            "best coworking spaces {city}",
            "startup {city}",
            "AI {city}",
            "{city} tech meetups",
            "{city} tech jobs",
            "{city} startup jobs",
            "{city} startups",
            "VC {city}",
            "founders {city}",
            "startup founders {city}",
            "{city} founder community",
            "how to find a cofounder {city}",
            "{city} entrepreneur",
        ],
        "de": [
            "KI-Unternehmen {city}",
            "beste Tech-Startups {city}",
            "Berliner Startup-Szene",
            "Tech Jobs {city}",
            "Fintech {city}",
            "Venture Capital {city}",
            "KI Jobs {city}",
            "beste Coworking Spaces {city}",
            "Startup {city}",
            "KI {city}",
            "Tech Meetups {city}",
            "Startup Jobs {city}",
            "Berliner Startups",
            "Gründer {city}",
            "Startup Gründer {city}",
            "Mitgründer finden {city}",
            "Unternehmer {city}",
        ],
        "fr": [
            "entreprises IA {city}",
            "meilleures startups tech {city}",
            "scène startup {city}",
            "travailler dans la tech {city}",
            "fintech {city}",
            "capital risque {city}",
            "emplois IA {city}",
            "meilleurs espaces de coworking {city}",
            "startup {city}",
            "IA {city}",
            "meetups tech {city}",
            "emplois tech {city}",
            "emplois startup {city}",
            "startups {city}",
            "VC {city}",
            "fondateurs {city}",
            "fondateurs de startups {city}",
            "communauté de fondateurs {city}",
            "trouver un cofondateur {city}",
            "entrepreneur {city}",
        ],
    },

    "culture": {
        "en": [
            "best clubs in {city}",
            "things to do in {city}",
            "{city} art scene",
            "{city} music festivals",
            "{city} nightlife",
            "{city} art galleries",
            "{city} clubs",
            "{city} fashion week",
            "{city} museums",
            "{city} festivals",
            "culture {city}",
            "{city} theater",
            "{city} opera",
            "{city} live music",
            "{city} contemporary art",
        ],
        "de": [
            "beste Clubs in {city}",
            "Freizeitaktivitäten {city}",
            "Berliner Kunstszene",
            "Musikfestivals {city}",
            "Berliner Nachtleben",
            "Kunstgalerien {city}",
            "Clubs {city}",
            "Fashion Week {city}",
            "Museen {city}",
            "Festivals {city}",
            "Kultur {city}",
            "Theater {city}",
            "Oper {city}",
            "Live-Musik {city}",
            "Zeitgenössische Kunst {city}",
        ],
        "fr": [
            "meilleures boîtes de nuit {city}",
            "activités à {city}",
            "scène artistique {city}",
            "festivals de musique {city}",
            "vie nocturne {city}",
            "galeries d'art {city}",
            "clubs {city}",
            "Fashion Week {city}",
            "musées {city}",
            "festivals {city}",
            "culture {city}",
            "théâtre {city}",
            "opéra {city}",
            "musique live {city}",
            "art contemporain {city}",
        ],
    },

    "fashion": {
        "en": [
            "vintage shops {city}",
            "{city} fashion week",
            "sustainable fashion {city}",
            "streetwear {city}",
            "best second hand {city}",
            "{city} designers",
            "where to shop in {city}",
            "{city} style",
            "{city} shopping",
            "{city} boutique",
            "{city} aesthetic",
        ],
        "de": [
            "Vintage Läden {city}",
            "Fashion Week {city}",
            "Nachhaltige Mode {city}",
            "Streetwear {city}",
            "beste Second-Hand Läden {city}",
            "Berliner Designer",
            "Einkaufen in {city}",
            "Berliner Stil",
            "Shopping {city}",
            "Boutiquen {city}",
            "Berliner Ästhetik",
        ],
        "fr": [
            "vintage {city}",
            "Fashion Week {city}",
            "mode durable {city}",
            "streetwear {city}",
            "meilleures friperies {city}",
            "créateurs de mode {city}",
            "où faire du shopping {city}",
            "style parisien",
            "shopping {city}",
            "boutiques {city}",
            "esthétique parisienne",
        ],
    },

    "food": {
        "en": [
            "best restaurants {city}",
            "{city} food scene",
            "where to eat in {city}",
            "{city} street food",
            "{city} coffee shops",
            "{city} brunch",
            "vegan {city}",
            "sustainable restaurants {city}",
            "{city} fine dining",
            "{city} food market",
            "{city} michelin star restaurants",
        ],
        "de": [
            "beste Restaurants {city}",
            "Berliner Gastronomieszene",
            "Essen gehen {city}",
            "Streetfood {city}",
            "Cafés {city}",
            "Brunch {city}",
            "Vegan {city}",
            "Nachhaltige Restaurants {city}",
            "Fine Dining {city}",
            "Wochenmarkt {city}",
            "Michelin Sterne Restaurants {city}",
        ],
        "fr": [
            "meilleurs restaurants {city}",
            "scène gastronomique {city}",
            "où manger à {city}",
            "street food {city}",
            "cafés {city}",
            "brunch {city}",
            "végane {city}",
            "restaurants durables {city}",
            "gastronomie {city}",
            "marché alimentaire {city}",
            "restaurants étoilés {city}",
        ],
    },

    "travel": {
        "en": [
            "weekend trips from {city}",
            "day trips from {city}",
            "best cities to visit from {city}",
            "flights from {city}",
            "cheap flights from {city}",
            "road trip from {city}",
            "train trips from {city}",
        ],
        "de": [
            "Wochenendausflug von {city}",
            "Tagesausflug von {city}",
            "beste Reiseziele von {city}",
            "Flüge ab {city}",
            "Billigflüge ab {city}",
            "Roadtrip von {city}",
            "Zugreisen von {city}",
        ],
        "fr": [
            "escapades du week-end depuis {city}",
            "excursions d'une journée depuis {city}",
            "meilleures villes à visiter depuis {city}",
            "vols depuis {city}",
            "vols pas chers depuis {city}",
            "road trip depuis {city}",
            "voyages en train depuis {city}",
        ],
    },
}

# ── Per-city seed overrides ───────────────────────────────────────────────────
# Some short "{city} + word" templates get hijacked by unrelated searches in one
# city only (e.g. "founders New York" → New York Giants / New York Times /
# New Yorker). Overrides swap the template for a tighter query in that city,
# leaving the shared templates (and other cities' history) untouched.
# Keys are templates exactly as written in SEED_TEMPLATES. Added 2026-W42.

SEED_OVERRIDES = {
    "new_york": {
        "{city} boutique":         "clothing boutiques {city}",   # was: hotels, law firms, talent agencies
        "{city} style":            "{city} street style",         # was: pizza crust, cheesecake
        "founders {city}":         "founders NYC",                # was: Giants, NY Post, New Yorker
        "startup founders {city}": "startup founders NYC",        # was: no suggestions at all
        "VC {city}":               "VC firms {city}",             # was: bedding, "vs New York" sports
        "culture {city}":          "{city} culture",              # was: froyo, jeans brand
        "AI {city}":               "AI events {city}",            # was: NYT / New Yorker articles
        "startup {city}":          "startup {city} City",         # was: Giants QB, Knicks
    },
}


def get_seeds(city_key: str) -> dict:
    """
    Returns a flat dict of {domain: [resolved_query_strings]}
    for a given city, combining English + local language seeds.
    City name is substituted into all {city} placeholders.
    """
    city = CITIES[city_key]
    display = city["display_name"]
    local_lang = city["hl_local"]
    overrides = SEED_OVERRIDES.get(city_key, {})

    result = {}
    for domain, lang_seeds in SEED_TEMPLATES.items():
        seeds = []

        # English seeds — always included
        for s in lang_seeds.get("en", []):
            s = overrides.get(s, s)
            seeds.append(s.replace("{city}", display))

        # Local language seeds — only if city has a local language
        if local_lang and local_lang in lang_seeds:
            for s in lang_seeds[local_lang]:
                seeds.append(s.replace("{city}", display))

        result[domain] = seeds

    return result


if __name__ == "__main__":
    # Quick sanity check
    for city_key in CITIES:
        seeds = get_seeds(city_key)
        total = sum(len(v) for v in seeds.values())
        print(f"\n{CITIES[city_key]['display_name']} — {total} seeds total")
        for domain, queries in seeds.items():
            print(f"  {domain}: {len(queries)}")
