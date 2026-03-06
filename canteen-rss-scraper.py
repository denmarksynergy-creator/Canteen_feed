# import modules
import time
import datetime
import pytz
import re
from hashlib import sha1

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator


# ============================
# Configuration
# ============================

# Prefer English when both DA/EN versions of same dish appear
PREFER_ENGLISH = True

# Base site English and Danish URLs
BASE_EN = "https://hubnordic.madkastel.dk/en/menu"
BASE_DA = "https://hubnordic.madkastel.dk/menu"

# Hub-specific pages (English first, Danish fallback tried automatically)
HUB_PAGES = {
    "HUB1 – Kays": [f"{BASE_EN}/hub1", f"{BASE_DA}/hub1"],
    "HUB2":        [f"{BASE_EN}/hub2", f"{BASE_DA}/hub2"],
    "HUB3":        [f"{BASE_EN}/hub3", f"{BASE_DA}/hub3"],
    # Foodcourt page contains Globetrotter/Homebound/Sprout sections
    "FOODCOURT":   [f"{BASE_EN}/foodcourt", f"{BASE_DA}/foodcourt"],
}

# RSS output
FEED_URL = "https://denmarksynergy-creator.github.io/Canteen_feed/feed.xml"
RSS_FILE = "feed.xml"

# Weekday maps (EN/DA → DA)
DAY_MAP = {
    "monday": "mandag",   "tuesday": "tirsdag",  "wednesday": "onsdag",
    "thursday": "torsdag","friday": "fredag",
    "mandag": "mandag",   "tirsdag": "tirsdag",  "onsdag": "onsdag",
    "torsdag": "torsdag", "fredag": "fredag",
}
VALID_DAYS = list(DAY_MAP.keys())
DAILY_DAYS_DA = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag"]

RESTAURANTS_FOODCOURT = ["globetrotter", "homebound", "sprout"]

# For robust concatenated-day handling
DAY_TOKENS_EN = ["monday", "tuesday", "wednesday", "thursday", "friday"]
DAY_TOKENS_DA = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag"]
DAY_TOKENS_ALL = DAY_TOKENS_EN + DAY_TOKENS_DA
DAY_REGEX = re.compile(r"(" + "|".join(DAY_TOKENS_ALL) + r")", flags=re.IGNORECASE)

# Green/Vegetarian markers in EN & DA
GREEN_MARKERS = [
    r"greendish", r"green\s*dish",     # EN variants
    r"grøn\s*ret",                     # DA
    r"vegetarian", r"vegetar",         # legacy veg markers
]


# ============================
# Selenium setup & fetch
# ============================

def setup_driver():
    """Configure and return a headless Chrome WebDriver."""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=chrome_options)
    driver.implicitly_wait(30)
    return driver


def fetch_page(urls):
    """Try each URL variant until one loads. Return HTML or None."""
    driver = setup_driver()
    html = None
    for url in urls:
        try:
            driver.get(url)
            try:
                WebDriverWait(driver, 40).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "main, article"))
                )
            except Exception:
                pass
            time.sleep(1.2)
            html = driver.page_source
            if html and len(html) > 1000:
                break
        except Exception as e:
            print(f"Failed to load {url}: {e}")
            continue
    driver.quit()
    return html


# ============================
# Utilities & filtering
# ============================

def clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s

def split_into_lines(div) -> list:
    """
    Extract visible text lines preserving <br/> boundaries so that:
      <p><strong>THURSDAY</strong> Panfried ...<br/>with ...</p>
    becomes: ["THURSDAY Panfried ...", "with ..."]
    """
    lines = []
    for el in div.find_all(["p", "li", "h1", "h2", "h3", "h4", "strong", "em"]):
        txt = el.get_text(separator="\n", strip=True)
        if not txt:
            continue
        for sub in txt.splitlines():
            sub = clean_text(sub)
            if sub:
                lines.append(sub)
    if not lines:
        raw = div.get_text(separator="\n", strip=True)
        for line in raw.splitlines():
            line = clean_text(line)
            if line:
                lines.append(line)
    return lines

def tidy_line(s: str) -> str:
    """Remove zero-width chars, trailing pipes, and extra spaces."""
    s = s.replace("\u200d", "")
    s = re.sub(r"\s*\|\s*$", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

# Expanded boilerplate patterns: newsletter, hours, addresses, phone, CVR, “Print menu”, etc.
BOILERPLATE_PATTERNS = [
    r"^Sign up for\b.*",                     # generic 'Sign up for …'
    r"^Tilmeld dig\b.*",                     # Danish generic

    r"Sign up for our newsletter",
    r"Tilmeld dig vores nyhedsbrev",
    r"Opening hours|Åbningstider",
    r"Kontrolrapport",
    r"Madkastellet Kantiner ApS",
    r"hubnordic@madkastellet\.dk",
    r"Copyright ©",
    r"Book selskab|Send request with date|booking@restaurantmark\.dk",
    r"Privatlivspolitik|Opdater cookie præferencer",
    r"Cafe Nabo",
    r"Week\s*\d+|Uge\s*\d+",
    r"\bHUB\s*[123]\b",
    r"We always offer a selected variety of daily dishes",
    r"It is always the menu in the restaurant that applies",
    r"Print menu",
    r"\u200d",  # zero-width joiner
    r"\.\.\.\s*and be among the first to receive notifications",

    # Opening hours / time ranges (several formats)
    r"^\s*Lunch\s+\d{1,2}(\:\d{2})?\s*a\.m\.\s*—\s*\d{1,2}(\:\d{2})?\s*p\.m\.\s*$",
    r"^\s*\d{1,2}(\:\d{2})?\s*a\.m\.\s*—\s*\d{1,2}(\:\d{2})?\s*p\.m\.\s*$",
    r"^\s*\d{1,2}(\:\d{2})?\s*a\.m\.\s*to\s*\d{1,2}(\:\d{2})?\s*p\.m\.\s*$",

    # Addresses / phone / CVR
    r"Slagtehusgade",
    r"København",
    r"Kay Fiskers Pl\.",
    r"\+45\s*\d{2}\s*\d{2}\s*\d{2}\s*\d{2}",
    r"\bCVR\b",
]

def is_boilerplate(text: str) -> bool:
    t = " ".join(text.split())
    for pat in BOILERPLATE_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return True
    return False

def should_hard_stop(line: str) -> bool:
    """
    If a line indicates the start of footer/newsletter block ('Sign up for', 'Tilmeld dig …'),
    we stop collecting any further lines for this hub.
    """
    low = tidy_line(line).lower()
    return low.startswith("sign up for") or low.startswith("tilmeld dig")


# ============================
# Day parsing (robust)
# ============================

def parse_days_from_line(text: str) -> list:
    """
    Return list of Danish weekday names referred to in the line, handling:
      - 'Monday, Tuesday' / 'Mandag, Tirsdag'
      - 'Onsdag/Wednesday'
      - 'Thursday and Friday' / 'Torsdag og Fredag'
      - 'MondayTuesday' / 'MandagTirsdag' (concatenated)
    """
    t_norm = tidy_line(text).lower()
    t_norm = t_norm.replace(",", " ").replace("/", " ").replace(":", " ")
    t_norm = t_norm.replace("–", " ").replace("-", " ")
    t_norm = re.sub(r"\band\b", " ", t_norm)
    t_norm = re.sub(r"\bog\b", " ", t_norm)

    days_found = set()

    for token in re.split(r"\s+", t_norm):
        token = token.strip()
        if not token:
            continue
        if token in DAY_MAP:
            days_found.add(DAY_MAP[token])
        else:
            for k in VALID_DAYS:
                if k in token:
                    days_found.add(DAY_MAP[k])
                    break

    t_compact = re.sub(r"\s+", "", t_norm)
    if t_compact:
        for m in DAY_REGEX.finditer(t_compact):
            day_token = m.group(1).lower()
            if day_token in DAY_MAP:
                days_found.add(DAY_MAP[day_token])

    ordered = [d for d in DAILY_DAYS_DA if d in days_found]
    return ordered


# ============================
# De-duplication helpers
# ============================

def norm_key(it: str) -> str:
    it = tidy_line(it)
    it = re.sub(r"[^\wÆØÅæøå\-.,;:()+/ ]", "", it)  # keep common punctuation and '+'
    return it.lower().strip()

def dedupe_list(items: list) -> list:
    seen = set()
    out = []
    for it in items:
        k = norm_key(it)
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out

def dedupe_items(menu_texts):
    """Deduplicate final items (title + first dish signature)."""
    seen = set()
    out = []
    for item in menu_texts:
        lines = [ln for ln in item.split("\n") if tidy_line(ln)]
        title_prefix = lines[0] if lines else ""
        first_dish = ""
        for ln in lines[1:]:
            tln = tidy_line(ln)
            if tln and not is_boilerplate(tln):
                first_dish = tln
                break
        sig = (norm_key(title_prefix), norm_key(first_dish))
        if sig not in seen:
            seen.add(sig)
            out.append(item)
    return out

def looks_english(s: str) -> bool:
    """Heuristic: English lines are pure ASCII (Danish often has æøå etc.)."""
    return not re.search(r"[^\x00-\x7F]", s)

def prefer_english_duplicates(lines: list) -> list:
    """
    If PREFER_ENGLISH is True, collapse DA/EN near-duplicates by keeping the English one.
    """
    if not PREFER_ENGLISH:
        return lines

    groups = {}
    order = []
    for ln in lines:
        sig = re.sub(r"[^\w]+", "", ln).lower()
        if sig not in groups:
            groups[sig] = []
            order.append(sig)
        groups[sig].append(ln)

    out = []
    for sig in order:
        candidates = groups[sig]
        if not candidates:
            continue
        # Prefer any English candidate; else keep the first
        en = [c for c in candidates if looks_english(c)]
        out.append(en[0] if en else candidates[0])
    return out


# ============================
# Consolidation helpers
# ============================

def is_day_header(line: str) -> bool:
    return bool(parse_days_from_line(line))

def is_green_marker(line: str) -> bool:
    low = tidy_line(line).lower()
    return any(re.search(p, low) for p in GREEN_MARKERS)

def consolidate_split_lines(raw_lines: list) -> list:
    """
    Consolidate:
      - 'Dish' + 'with/med/served/...' continuation lines
      - 'Greendish|Green dish|Grøn ret|Vegetarian|Vegetar' (with/without ':', inline or split)
      - Remove day headers later (not here)
    """
    CONNECTOR_PAT = r"^(with|med|served|accompanied|hertil|hereto|topped|og|and|finished)\b"

    def is_connector(line: str) -> bool:
        return bool(re.search(CONNECTOR_PAT, tidy_line(line).lower()))

    out = []
    i = 0
    n = len(raw_lines)

    while i < n:
        cur = tidy_line(raw_lines[i])
        if not cur or is_boilerplate(cur):
            i += 1
            continue

        # If line mixes day header + dish, strip header tokens first
        days_in = parse_days_from_line(cur)
        if days_in:
            # If pure header (short), keep for context (we drop later)
            if len(cur.split()) <= 3:
                out.append(cur)
                i += 1
                continue
            tmp = cur
            for tok in DAY_TOKENS_ALL:
                tmp = re.sub(rf"\b{tok}\b", "", tmp, flags=re.IGNORECASE)
            cur = tidy_line(tmp)

        low = cur.lower()

        # Inline GREEN/VEG without colon: "<label> <dish>"
        if is_green_marker(cur) and ":" not in cur and re.search(r"\s+\S+", cur):
            # Derive the dish part by dropping the first word(s) that match a marker
            # Use a coarse split at the first space
            parts = cur.split(None, 1)
            dish = tidy_line(parts[1]) if len(parts) > 1 else ""
            if dish:
                combined = f"GREEN DISH: {dish}"
                j = i + 1
                while j < n:
                    nxt = tidy_line(raw_lines[j])
                    if not nxt or is_boilerplate(nxt) or is_day_header(nxt) or is_green_marker(nxt):
                        break
                    if is_connector(nxt):
                        combined = tidy_line(f"{combined} {nxt}")
                        j += 1
                    else:
                        break
                out.append(combined)
                i = j
                continue

        # GREEN/VEG label-only (no colon, no dish on same line)
        if is_green_marker(cur) and ":" not in cur and not re.search(r"\s+\S+", cur):
            j = i + 1
            parts = []
            while j < n:
                nxt = tidy_line(raw_lines[j])
                if not nxt or is_boilerplate(nxt):
                    j += 1
                    continue
                if is_day_header(nxt) or is_green_marker(nxt):
                    break
                parts.append(nxt)
                j += 1
            if parts:
                out.append(f"GREEN DISH: {' '.join(parts)}")
            i = j
            continue

        # With colon: normalize to GREEN DISH:
        m_colon = re.match(r"^\s*(greendish|green\s*dish|grøn\s*ret|vegetarian|vegetar)\s*:\s*(.*)$",
                           low, flags=re.IGNORECASE)
        if m_colon:
            dish = tidy_line(cur[m_colon.start(2):])
            combined = f"GREEN DISH: {dish}" if dish else "GREEN DISH:"
            j = i + 1
            added_dish = bool(dish)
            while j < n:
                nxt = tidy_line(raw_lines[j])
                if not nxt or is_boilerplate(nxt) or is_day_header(nxt) or is_green_marker(nxt):
                    break
                if not added_dish:
                    combined = tidy_line(f"{combined} {nxt}")
                    added_dish = True
                    j += 1
                    continue
                if is_connector(nxt):
                    combined = tidy_line(f"{combined} {nxt}")
                    j += 1
                    continue
                break
            out.append(combined)
            i = j
            continue

        # General dish line: attach connector/follow-ups
        combined = cur
        j = i + 1
        while j < n:
            nxt = tidy_line(raw_lines[j])
            if not nxt or is_boilerplate(nxt) or is_day_header(nxt) or is_green_marker(nxt):
                break
            if is_connector(nxt):
                combined = tidy_line(f"{combined} {nxt}")
                j += 1
            else:
                break
        out.append(combined)
        i = j

    out = [tidy_line(x) for x in out if tidy_line(x)]
    out = dedupe_list(out)
    return out


# ============================
# Allergen key extraction
# ============================

def extract_allergen_key_from_html(html) -> list:
    """
    Find the 1..15 allergen list and return strings like '1. Gluten', '2. Eggs', ...
    Strategy:
      1) Prefer an <ol> near a heading that says 'Allergen' (EN) or 'Allergener' (DA).
      2) Otherwise, fallback to any <ol> with ~10–20 items containing known allergen words.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1) Look for a heading that contains 'Allergen' (EN/DA), then nearest following <ol>
    heads = soup.find_all(re.compile(r"^h[1-6]$"))
    for h in heads:
        title = clean_text(h.get_text(" ", strip=True)).lower()
        if re.search(r"\ballergen", title):  # matches 'Allergen', 'Allergens', 'Allergener'
            for sib in h.find_all_next(["ol"]):
                lis = [clean_text(li.get_text(" ", strip=True)) for li in sib.find_all("li")]
                if 10 <= len(lis) <= 20:
                    return [f"{i+1}. {txt}" for i, txt in enumerate(lis)]

    # 2) Fallback: any <ol> with 10–20 items and allergen terms
    for ol in soup.find_all("ol"):
        lis = [clean_text(li.get_text(" ", strip=True)) for li in ol.find_all("li")]
        if 10 <= len(lis) <= 20 and any(
            re.search(r"gluten|milk|soy|egg|sesame|nuts|mustard|celery|peanut|lupine|sulphur|mollusc|fish|garlic", li, re.I)
            for li in lis
        ):
            return [f"{i+1}. {txt}" for i, txt in enumerate(lis)]

    return []


# ============================
# Parsers
# ============================

def parse_hub_page(html):
    """Parse a HUB page (HUB1/HUB2/HUB3). Return dict(day -> [items])."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("main") or soup.select_one("article") or soup.select_one("div.entry-content")
    if not content:
        content = soup  # fallback

    block_menus = {}
    current_day = None
    current_days_multi = []
    collected_common = []

    for line in split_into_lines(content):
        line = tidy_line(line)
        if not line or is_boilerplate(line):
            continue

        found_days = parse_days_from_line(line)
        if found_days and len(line.split()) <= 3:
            current_day = found_days[0]
            current_days_multi = found_days[:]
            block_menus.setdefault(current_day, [])
            continue
        elif found_days:
            current_day = found_days[0]
            current_days_multi = found_days[:]
            block_menus.setdefault(current_day, [])
            tmp = line
            for tok in DAY_TOKENS_ALL:
                tmp = re.sub(rf"\b{tok}\b", "", tmp, flags=re.IGNORECASE)
            tmp = tidy_line(tmp)
            if tmp:
                for d in current_days_multi:
                    block_menus.setdefault(d, []).append(tmp)
            continue

        targets = current_days_multi if current_days_multi else ([current_day] if current_day else [])
        if targets:
            for d in targets:
                block_menus.setdefault(d, []).append(line)
        else:
            collected_common.append(line)

    if not block_menus and collected_common:
        for d in DAILY_DAYS_DA:
            block_menus[d] = collected_common[:]
    else:
        for d in DAILY_DAYS_DA:
            block_menus.setdefault(d, [])
            for item in collected_common:
                if norm_key(item) not in set(map(norm_key, block_menus[d])):
                    block_menus[d].append(item)

    for d in list(block_menus.keys()):
        block_menus[d] = dedupe_list(block_menus[d])

    return block_menus


def parse_foodcourt_page(html):
    """Parse Foodcourt page → returns dict per restaurant (Globetrotter/Homebound/Sprout) with day→items."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("main") or soup.select_one("article") or soup.select_one("div.entry-content")
    if not content:
        content = soup

    lines = split_into_lines(content)

    restaurant_menus = {}
    current_restaurant = None
    current_days = []
    sprout_buffer = []

    def ensure_rest_day_entries(name, days):
        restaurant_menus.setdefault(name, {})
        for d in days:
            restaurant_menus[name].setdefault(d, [])

    for raw in lines:
        line = tidy_line(raw)
        low = line.lower()

        if not line or is_boilerplate(line):
            continue

        # Restaurant headers
        found_rest = None
        for r in RESTAURANTS_FOODCOURT:
            if re.search(rf"\b{re.escape(r)}\b", low):
                found_rest = r.capitalize()
                break
        if found_rest:
            current_restaurant = found_rest
            current_days = []
            ensure_rest_day_entries(current_restaurant, DAILY_DAYS_DA)
            continue

        # Day ranges / concatenated
        found_days = parse_days_from_line(line)
        if found_days and len(line.split()) <= 3:
            current_days = found_days
            ensure_rest_day_entries(current_restaurant, current_days)
            continue
        elif found_days:
            current_days = found_days
            ensure_rest_day_entries(current_restaurant, current_days)
            tmp = line
            for tok in DAY_TOKENS_ALL:
                tmp = re.sub(rf"\b{tok}\b", "", tmp, flags=re.IGNORECASE)
            tmp = tidy_line(tmp)
            if tmp:
                for d in current_days:
                    restaurant_menus[current_restaurant][d].append(tmp)
            continue

        # Sprout daily salad
        if current_restaurant == "Sprout":
            if re.search(r"(salad|salat|protein|dagens|daily meal)", low):
                sprout_buffer.append(line)
            continue

        # Collect lines (no label injection here)
        if current_restaurant and line and not any(r in low for r in RESTAURANTS_FOODCOURT):
            days_target = current_days if current_days else DAILY_DAYS_DA
            for d in days_target:
                restaurant_menus[current_restaurant][d].append(line)

    if sprout_buffer:
        restaurant_menus.setdefault("Sprout", {})
        for d in DAILY_DAYS_DA:
            restaurant_menus["Sprout"][d] = sprout_buffer[:]

    for rname, days in restaurant_menus.items():
        for d, items in list(days.items()):
            restaurant_menus[rname][d] = dedupe_list(items)

    return restaurant_menus


# ============================
# End-to-end scraping
# ============================

def scrape_weekly_menus():
    """Scrape HUB1, HUB2, HUB3, and Foodcourt; also return allergen key if present."""
    menus_by_hub = {}
    allergen_key = []

    for hub_name, urls in HUB_PAGES.items():
        html = fetch_page(urls)
        if not html:
            print(f"Warning: could not load any URL for {hub_name}: {urls}")
            continue

        # Try to extract allergen key once (any page)
        if not allergen_key:
            ak = extract_allergen_key_from_html(html)
            if ak:
                allergen_key = ak

        if hub_name == "FOODCOURT":
            fc_menus = parse_foodcourt_page(html)
            for rname, days in fc_menus.items():
                menus_by_hub[rname] = days
        else:
            block_menus = parse_hub_page(html)
            menus_by_hub[hub_name] = block_menus

    print("Scraped menus by hub:", menus_by_hub)
    if allergen_key:
        print("Extracted allergen key:", allergen_key)
    return menus_by_hub, allergen_key


# ============================
# Today’s menus (build per hub)
# ============================

def get_today_menus(menus_by_hub):
    weekday_mapping = {
        "Monday": "mandag", "Tuesday": "tirsdag", "Wednesday": "onsdag",
        "Thursday": "torsdag", "Friday": "fredag",
        "Saturday": "lørdag", "Sunday": "søndag",
    }
    today_en = datetime.datetime.today().strftime("%A")
    today_da = weekday_mapping.get(today_en, "").lower()
    print("Systemets dag (engelsk):", today_en)
    print("Mapper til (dansk):", today_da)

    target_hubs = {"HUB1 – Kays", "Homebound", "HUB2", "HUB3", "Globetrotter", "Sprout"}
    today_menus = []

    for hub, menu_dict in menus_by_hub.items():
        if hub not in target_hubs:
            continue

        items_today = menu_dict.get(today_da, [])
        if not items_today:
            continue

        # Consolidate raw lines (merge split dishes, normalize GREEN DISH)
        consolidated = consolidate_split_lines(items_today)

        # Filter, hard-stop, prefer English (optional), de-dup
        seen = set()
        unique_menu = []
        for item in consolidated:
            # HARD STOP: anything after "Sign up for / Tilmeld dig" must be ignored
            if should_hard_stop(item):
                break

            normalized = tidy_line(item)
            if not normalized:
                continue
            if is_day_header(normalized):
                continue
            if is_boilerplate(normalized):
                continue

            k = norm_key(normalized)
            if k not in seen:
                seen.add(k)
                unique_menu.append(normalized)

        if not unique_menu:
            continue

        # Prefer English over Danish duplicates (if enabled)
        unique_menu = prefer_english_duplicates(unique_menu)

        formatted_menu = [f"   {ln} | " for ln in unique_menu]
        menu_text = "\n".join(tidy_line(x) for x in formatted_menu if tidy_line(x))

        if hub in ["Homebound", "Globetrotter", "Sprout"]:
            today_menus.append(f"🍽 HUB1 - {hub} Lunch Menu:  | \n{menu_text}")
        else:
            today_menus.append(f"🍽 {hub} - Lunch Menu:  | \n{menu_text}")

    return today_menus


# ============================
# RSS generation
# ============================

def summarize_title(item: str) -> str:
    first_line = item.split("\n", 1)[0]
    return tidy_line(first_line) or "Today's Menu"

def long_body(item: str) -> str:
    parts = item.split("\n")
    if len(parts) <= 1:
        return ""
    body_lines = parts[1:]
    clean = []
    for ln in body_lines:
        t = tidy_line(ln)
        if t and not is_boilerplate(t):
            clean.append(t)
    return "\n".join(clean)

def generate_rss(menu_items, allergen_key=None):
    fg = FeedGenerator()
    today_str = datetime.date.today().strftime("%A, %d %B %Y")

    fg.title(f"Canteen Menu - {today_str}")
    fg.link(href="https://hubnordic.madkastel.dk/en/menu", rel="alternate")
    fg.link(href=FEED_URL, rel="self", type="application/rss+xml")
    fg.description("Daily updated canteen-menu")
    fg.language("da")
    fg.lastBuildDate(datetime.datetime.now(pytz.utc))
    fg.generator("Python feedgen")
    fg.ttl(15)
    fg.docs("http://www.rssboard.org/rss-specification")

    # Menu items
    for item in menu_items:
        title_text = summarize_title(item)
        body_text  = long_body(item)
        description_full = title_text if body_text.strip() == "" else f"{title_text}\n{body_text}"

        entry = fg.add_entry()
        entry.title(title_text)
        entry.link(href="https://hubnordic.madkastel.dk/en/menu")
        entry.description(description_full)
        entry.pubDate(datetime.datetime.now(pytz.utc))
        guid_hash = sha1(description_full.encode("utf-8")).hexdigest()[:16]
        guid_value = f"urn:canteen:{guid_hash}-{datetime.datetime.now().strftime('%Y%m%d')}"
        entry.guid(guid_value, permalink=False)

    # Allergen key item (once, if available)
    if allergen_key:
        body = "\n".join(allergen_key)
        entry = fg.add_entry()
        entry.title("Allergen key (1–15)")
        entry.link(href="https://hubnordic.madkastel.dk/en/menu")
        entry.description(f"Allergen key\n{body}")
        entry.pubDate(datetime.datetime.now(pytz.utc))
        guid_hash = sha1(("allergen" + body).encode("utf-8")).hexdigest()[:16]
        guid_value = f"urn:canteen:{guid_hash}-{datetime.datetime.now().strftime('%Y%m%d')}"
        entry.guid(guid_value, permalink=False)

    rss_bytes = fg.rss_str(pretty=True)
    rss_str = rss_bytes.decode("utf-8")
    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(rss_str)
    print("RSS feed updated")


# ============================
# Main
# ============================

if __name__ == "__main__":
    menus_by_hub, allergen_key = scrape_weekly_menus()

    # Optional debug:
    # weekday_mapping = {"Monday":"mandag","Tuesday":"tirsdag","Wednesday":"onsdag","Thursday":"torsdag","Friday":"fredag"}
    # today_da = weekday_mapping[datetime.datetime.today().strftime("%A")]
    # for hub, dct in menus_by_hub.items():
    #     raw_today = dct.get(today_da, [])
    #     print(f"\nDEBUG {hub} raw_today:")
    #     for ln in raw_today: print("  ", ln)
    #     cons = consolidate_split_lines(raw_today)
    #     print(f"DEBUG {hub} consolidated:")
    #     for ln in cons: print("  ", ln)

    today_menus = get_today_menus(menus_by_hub)
    today_menus = dedupe_items(today_menus)

    print("Today's menu:")
    for menu in today_menus:
        print(menu, end="\n\n")

    generate_rss(today_menus, allergen_key=allergen_key)