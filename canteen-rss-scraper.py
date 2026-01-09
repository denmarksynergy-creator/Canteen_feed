
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


# ----------------------------
# Constants & Configuration
# ----------------------------

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

# URL for the RSS feed (self link)
FEED_URL = "https://denmarksynergy-creator.github.io/Canteen_feed/feed.xml"
# Local file in repo to save the RSS feed
RSS_FILE = "feed.xml"

# Weekday maps
DAY_MAP = {
    # English -> Danish
    "monday": "mandag",
    "tuesday": "tirsdag",
    "wednesday": "onsdag",
    "thursday": "torsdag",
    "friday": "fredag",
    # Danish (normalized to themselves)
    "mandag": "mandag",
    "tirsdag": "tirsdag",
    "onsdag": "onsdag",
    "torsdag": "torsdag",
    "fredag": "fredag",
}
VALID_DAYS = list(DAY_MAP.keys())
DAILY_DAYS_DA = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag"]

RESTAURANTS_FOODCOURT = ["globetrotter", "homebound", "sprout"]

# For robust concatenated-day handling
DAY_TOKENS_EN = ["monday", "tuesday", "wednesday", "thursday", "friday"]
DAY_TOKENS_DA = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag"]
DAY_TOKENS_ALL = DAY_TOKENS_EN + DAY_TOKENS_DA
DAY_REGEX = re.compile(r"(" + "|".join(DAY_TOKENS_ALL) + r")", flags=re.IGNORECASE)


# ----------------------------
# Selenium setup & page fetch
# ----------------------------

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
                # still try to get page_source
                pass
            time.sleep(1.5)
            html = driver.page_source
            if html and len(html) > 1000:  # basic sanity
                break
        except Exception as e:
            print(f"Failed to load {url}: {e}")
            continue
    driver.quit()
    return html


# ----------------------------
# Text utilities & filtering
# ----------------------------

def clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_into_lines(div) -> list:
    """Extract visible text lines (<p>, list items, headings) in order."""
    lines = []
    for el in div.find_all(["p", "li", "h1", "h2", "h3", "h4", "strong"]):
        txt = el.get_text(separator=" ", strip=True)
        txt = clean_text(txt)
        if txt:
            lines.append(txt)
    # Fall back: if content area is not structured
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
    s = re.sub(r"\s*\|\s*$", "", s)  # remove trailing pipes
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


# Patterns commonly seen in the menu pages that are not actual menu items
BOILERPLATE_PATTERNS = [
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
]

def is_boilerplate(text: str) -> bool:
    t = " ".join(text.split())
    for pat in BOILERPLATE_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return True
    return False


# ----------------------------
# Day parsing (robust)
# ----------------------------

def parse_days_from_line(text: str) -> list:
    """
    Parse one line and return a list of Danish weekday names it refers to.
    Handles:
      - 'Mandag, Tirsdag' / 'Monday, Tuesday'
      - 'Onsdag/Wednesday'
      - 'Torsdag og Fredag' / 'Thursday and Friday'
      - Concatenated: 'MondayTuesday', 'MandagTirsdag' (no separators)
    Returns: list of Danish day names in Mon→Fri order, e.g. ['mandag','tirsdag'].
    """
    # 1) Normalize common separators/words
    t_norm = tidy_line(text).lower()
    t_norm = t_norm.replace(",", " ")
    t_norm = t_norm.replace("/", " ")
    t_norm = t_norm.replace(":", " ")
    t_norm = t_norm.replace("–", " ").replace("-", " ")
    t_norm = re.sub(r"\band\b", " ", t_norm)   # English 'and'
    t_norm = re.sub(r"\bog\b", " ", t_norm)    # Danish 'og'

    days_found = set()

    # 2) Token-based detection (spaces present)
    for token in re.split(r"\s+", t_norm):
        token = token.strip()
        if not token:
            continue
        if token in DAY_MAP:              # exact match
            days_found.add(DAY_MAP[token])
        else:
            # fuzzy: 'tirsdag.' etc.
            for k in VALID_DAYS:
                if k in token:
                    days_found.add(DAY_MAP[k])
                    break

    # 3) Concatenated detection: remove all spaces and scan for day names back-to-back
    t_compact = re.sub(r"\s+", "", t_norm)
    if t_compact:
        for m in DAY_REGEX.finditer(t_compact):
            day_token = m.group(1).lower()
            if day_token in DAY_MAP:
                days_found.add(DAY_MAP[day_token])
            else:
                for k in VALID_DAYS:
                    if k == day_token:
                        days_found.add(DAY_MAP[k])
                        break

    # 4) Keep only Monday–Friday and preserve stable order
    ordered = [d for d in DAILY_DAYS_DA if d in days_found]
    return ordered


# ----------------------------
# Dedup helpers
# ----------------------------

def norm_key(it: str) -> str:
    """Stronger normalization key for de-duplication."""
    it = tidy_line(it)
    it = re.sub(r"[^\wÆØÅæøå\-.,;:() ]", "", it)  # strip odd chars, keep common punctuation
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


# ----------------------------
# Consolidation helpers
# ----------------------------

def is_day_header(line: str) -> bool:
    """Return True if a line looks like a day header (including multi-day and mixed language forms)."""
    return bool(parse_days_from_line(line))

def consolidate_split_lines(raw_lines: list) -> list:
    """
    Consolidate split dish lines and all vegetarian patterns into single logical dish lines.
    Handles:
      - Meat/general: 'Dish name' + 'with/med/served/...' continuation lines
      - Vegetarian label-only: 'Vegetarian' / 'Vegetar' (+ next lines) -> 'VEGETARIAN: <dish ...>'
      - Vegetarian with colon: 'Vegetarian:' + dish + optional 'with/med/...' continuation
      - Inline 'Vegetarian <dish>' (no colon) -> 'VEGETARIAN: <dish>'
      - Stops consolidation when the next line is a new day header or a new vegetarian label
    """
    CONNECTOR_PAT = r"^(with|med|served|accompanied|hertil|hereto|topped|og|and|finished)\b"

    def is_connector(line: str) -> bool:
        return bool(re.search(CONNECTOR_PAT, tidy_line(line).lower()))

    def is_veg_label(line: str) -> bool:
        low = tidy_line(line).lower()
        return low in ["vegetarian", "vegetar", "vegetarian:", "vegetar:"]

    out = []
    i = 0
    n = len(raw_lines)

    while i < n:
        cur = tidy_line(raw_lines[i])
        if not cur or is_boilerplate(cur):
            i += 1
            continue

        # Day header: keep for now (we'll drop later in get_today_menus)
        if is_day_header(cur):
            out.append(cur)
            i += 1
            continue

        # Inline "Vegetarian <dish>" (no colon)
        m_inline = re.match(r"^\s*(vegetar|vegetarian)\s+(.*)$", cur, flags=re.IGNORECASE)
        if m_inline and ":" not in cur:
            dish = tidy_line(m_inline.group(2))
            if dish:
                combined = f"{m_inline.group(1).strip().upper()}: {dish}"
                # attach connector next lines
                j = i + 1
                while j < n:
                    nxt = tidy_line(raw_lines[j])
                    if not nxt or is_boilerplate(nxt) or is_day_header(nxt) or is_veg_label(nxt):
                        break
                    if is_connector(nxt):
                        combined = tidy_line(f"{combined} {nxt}")
                        j += 1
                    else:
                        break
                out.append(combined)
                i = j
                continue

        # Vegetarian label-only (no colon): absorb following lines
        if is_veg_label(cur) and ":" not in cur:
            j = i + 1
            parts = []
            while j < n:
                nxt = tidy_line(raw_lines[j])
                if not nxt or is_boilerplate(nxt):
                    j += 1
                    continue
                if is_day_header(nxt) or is_veg_label(nxt):
                    break
                parts.append(nxt)
                j += 1
            if parts:
                out.append(f"{cur.strip().upper()}: {' '.join(parts)}")
            i = j
            continue

        # Vegetarian with colon: attach dish + connectors
        m_colon = re.match(r"^\s*(vegetar|vegetarian)\s*:\s*(.*)$", cur, flags=re.IGNORECASE)
        if m_colon:
            label = m_colon.group(1).strip().upper()
            dish = tidy_line(m_colon.group(2))
            combined = f"{label}: {dish}" if dish else f"{label}:"
            j = i + 1
            added_dish = bool(dish)
            while j < n:
                nxt = tidy_line(raw_lines[j])
                if not nxt or is_boilerplate(nxt) or is_day_header(nxt) or is_veg_label(nxt):
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
            if not nxt or is_boilerplate(nxt) or is_day_header(nxt) or is_veg_label(nxt):
                break
            if is_connector(nxt):
                combined = tidy_line(f"{combined} {nxt}")
                j += 1
            else:
                break
        out.append(combined)
        i = j

    # Final tidy and de-dup
    out = [tidy_line(x) for x in out if tidy_line(x)]
    out = dedupe_list(out)
    return out


# ----------------------------
# Parsers
# ----------------------------

def parse_hub_page(html):
    """Parse a HUB page (HUB1/HUB2/HUB3). Return dict(day -> [items])."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("main") or soup.select_one("article") or soup.select_one("div.entry-content")
    if not content:
        content = soup  # fallback to whole document

    block_menus = {}
    current_day = None
    current_days_multi = []
    collected_common = []

    for line in split_into_lines(content):
        line = tidy_line(line)

        # Skip boilerplate early
        if not line or is_boilerplate(line):
            continue

        # Identify day headers (including multi-day lines)
        found_days = parse_days_from_line(line)
        if found_days:
            current_day = found_days[0]
            current_days_multi = found_days[:]  # apply following dishes to all found days
            block_menus.setdefault(current_day, [])
            continue

        # Do NOT inject vegetarian labels here; just collect raw lines
        targets = current_days_multi if current_days_multi else ([current_day] if current_day else [])
        if targets:
            for d in targets:
                block_menus.setdefault(d, []).append(line)
        else:
            collected_common.append(line)

    # If a page only lists items without day headers, spread them across all days
    if not block_menus and collected_common:
        for d in DAILY_DAYS_DA:
            block_menus[d] = collected_common[:]
    else:
        # Ensure every weekday exists (fill with common lines)
        for d in DAILY_DAYS_DA:
            block_menus.setdefault(d, [])
            for item in collected_common:
                if norm_key(item) not in set(map(norm_key, block_menus[d])):
                    block_menus[d].append(item)

    # Deduplicate per day (stronger normalization)
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

        # Skip boilerplate early
        if not line or is_boilerplate(line):
            continue

        # Detect restaurant heading
        found_rest = None
        for r in RESTAURANTS_FOODCOURT:
            if re.search(rf"\b{re.escape(r)}\b", low):
                found_rest = r.capitalize()
                break
        if found_rest:
            current_restaurant = found_rest
            current_days = []
            ensure_rest_day_entries(current_restaurant, DAILY_DAYS_DA)  # pre-create weekdays
            continue

        # Detect days for multi-day lines (supports comma/slash/og/and/concatenated)
        found_days = parse_days_from_line(line)
        if found_days and current_restaurant:
            current_days = found_days
            ensure_rest_day_entries(current_restaurant, current_days)
            continue

        # Sprout: keep the primary describe-the-salad line only
        if current_restaurant == "Sprout":
            if re.search(r"(salad|salat|protein|dagens)", low):
                sprout_buffer.append(line)
            continue

        # Do NOT inject veg labels; just collect raw lines
        if current_restaurant and line and not any(r in low for r in RESTAURANTS_FOODCOURT):
            days_target = current_days if current_days else DAILY_DAYS_DA
            for d in days_target:
                restaurant_menus[current_restaurant][d].append(line)

    # Assign Sprout to all days
    if sprout_buffer:
        restaurant_menus.setdefault("Sprout", {})
        for d in DAILY_DAYS_DA:
            restaurant_menus["Sprout"][d] = sprout_buffer[:]

    # Dedup per restaurant/day
    for rname, days in restaurant_menus.items():
        for d, items in list(days.items()):
            restaurant_menus[rname][d] = dedupe_list(items)

    return restaurant_menus


# ----------------------------
# End-to-end scraping
# ----------------------------

def scrape_weekly_menus():
    """Scrape HUB1, HUB2, HUB3, and Foodcourt into a single dict."""
    menus_by_hub = {}

    for hub_name, urls in HUB_PAGES.items():
        html = fetch_page(urls)
        if not html:
            print(f"Warning: could not load any URL for {hub_name}: {urls}")
            continue

        if hub_name == "FOODCOURT":
            fc_menus = parse_foodcourt_page(html)
            # Add each restaurant as its own 'hub' key (to match downstream logic)
            for rname, days in fc_menus.items():
                menus_by_hub[rname] = days
        else:
            block_menus = parse_hub_page(html)
            menus_by_hub[hub_name] = block_menus

    print("Scraped menus by hub:", menus_by_hub)
    return menus_by_hub


# ----------------------------
# Today’s menus (build per hub)
# ----------------------------

def get_today_menus(menus_by_hub):
    weekday_mapping = {
        "Monday": "mandag",
        "Tuesday": "tirsdag",
        "Wednesday": "onsdag",
        "Thursday": "torsdag",
        "Friday": "fredag",
        "Saturday": "lørdag",
        "Sunday": "søndag",
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

        # 1) Consolidate split lines and all vegetarian patterns
        consolidated = consolidate_split_lines(items_today)

        # 2) Filter out day headers & boilerplate; dedupe dishes
        seen = set()
        unique_menu = []
        for item in consolidated:
            normalized = tidy_line(item)
            if not normalized:
                continue

            # Remove day headers (not dishes)
            if is_day_header(normalized):
                continue

            # Skip boilerplate
            if is_boilerplate(normalized):
                continue

            k = norm_key(normalized)
            if k not in seen:
                seen.add(k)
                unique_menu.append(normalized)

        if not unique_menu:
            continue

        # 3) Format lines (already consolidated; no extra label injection)
        formatted_menu = [f"   {ln} | " for ln in unique_menu]
        menu_text = "\n".join(tidy_line(x) for x in formatted_menu if tidy_line(x))

        # 4) Append item once per hub
        if hub in ["Homebound", "Globetrotter", "Sprout"]:
            today_menus.append(f"🍽 HUB1 - {hub} Lunch Menu:  | \n{menu_text}")
        else:
            today_menus.append(f"🍽 {hub} - Lunch Menu:  | \n{menu_text}")

    return today_menus


# ----------------------------
# RSS generation (description only)
# ----------------------------

def summarize_title(item: str) -> str:
    """Title = first line only (tidied)."""
    first_line = item.split("\n", 1)[0]
    return tidy_line(first_line) or "Today's Menu"

def long_body(item: str) -> str:
    """
    Body = all lines after the first line, tidied and without boilerplate.
    Always returns a string (possibly empty), never None.
    """
    parts = item.split("\n")
    if len(parts) <= 1:
        return ""  # important: return empty string, not None
    body_lines = parts[1:]  # skip the first line (title)
    clean = []
    for ln in body_lines:
        t = tidy_line(ln)
        if t and not is_boilerplate(t):
            clean.append(t)
    return "\n".join(clean)

def generate_rss(menu_items):
    fg = FeedGenerator()
    today_str = datetime.date.today().strftime("%A, %d %B %Y")

    # Feed metadata
    fg.title(f"Canteen Menu - {today_str}")
    fg.link(href="https://hubnordic.madkastel.dk/en/menu", rel="alternate")
    fg.link(href=FEED_URL, rel="self", type="application/rss+xml")
    fg.description("Daily updated canteen-menu")
    fg.language("da")
    fg.lastBuildDate(datetime.datetime.now(pytz.utc))
    fg.generator("Python feedgen")
    fg.ttl(15)
    fg.docs("http://www.rssboard.org/rss-specification")

    for item in menu_items:
        title_text = summarize_title(item)
        body_text  = long_body(item)

        # Compose the description: repeat title + menu text
        description_full = title_text if body_text.strip() == "" else f"{title_text}\n{body_text}"

        entry = fg.add_entry()
        entry.title(title_text)
        entry.link(href="https://hubnordic.madkastel.dk/en/menu")
        entry.description(description_full)   # screens read description
        entry.pubDate(datetime.datetime.now(pytz.utc))

        # Compact GUID
        guid_hash = sha1(description_full.encode("utf-8")).hexdigest()[:16]
        guid_value = f"urn:canteen:{guid_hash}-{datetime.datetime.now().strftime('%Y%m%d')}"
        entry.guid(guid_value, permalink=False)

    rss_bytes = fg.rss_str(pretty=True)
    rss_str = rss_bytes.decode("utf-8")
    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(rss_str)
    print("RSS feed updated")


# ----------------------------
# Main
# ----------------------------

if __name__ == "__main__":
    menus_by_hub = scrape_weekly_menus()

    # Optional debug (uncomment to inspect today's raw + consolidated items per hub)
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

    generate_rss(today_menus)
