
import time
import datetime
import pytz
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

# Base site
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

# --- Utilities ---

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

def setup_driver():
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
            # Wait for the main content area; WordPress pages render <main> or article
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

def clean_text(s):
    s = re.sub(r"\s+", " ", s).strip()
    return s

def split_into_lines(div):
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

def normalize_day_token(text):
    t = clean_text(text).lower().replace(":", "")
    # Some pages prefix "Week X" lines; ignore those
    if t.startswith("week ") or t.startswith("uge "):
        return None
    # direct match for whole tokens
    if t in DAY_MAP:
        return DAY_MAP[t]
    # fuzzy: detect if a day word appears inside a line
    for k in VALID_DAYS:
        if k in t:
            return DAY_MAP[k]
    return None




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
    r"\u200d",  # zero-width joiner
]

def is_boilerplate(text: str) -> bool:
    t = " ".join(text.split())
    for pat in BOILERPLATE_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return True
    return False

def tidy_line(s: str) -> str:
    s = s.replace("\u200d", "")
    s = re.sub(r"\s*\|\s*$", "", s)  # remove trailing pipes
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s



def dedupe_items(menu_texts):
    seen = set()
    out = []
    for item in menu_texts:
        # signature: first line up to newline + first non-empty dish line
        lines = [ln for ln in item.split("\n") if tidy_line(ln)]
        title_prefix = lines[0] if lines else ""
        first_dish = ""
        for ln in lines[1:]:
            tln = tidy_line(ln)
            if tln and not is_boilerplate(tln):
                first_dish = tln
                break
        sig = (title_prefix.lower(), first_dish.lower())
        if sig not in seen:
            seen.add(sig)
            out.append(item)
    return out

   


# --- Parsers ---

def parse_hub_page(html):
    """Parse a HUB page (HUB1/HUB2/HUB3). Return dict(day -> [items])."""
    soup = BeautifulSoup(html, "html.parser")
    # Try typical WordPress content containers
    content = soup.select_one("main") or soup.select_one("article") or soup.select_one("div.entry-content")
    if not content:
        content = soup  # fallback to whole document

    block_menus = {}
    current_day = None
    collected_common = []

    for line in split_into_lines(content):
        # Identify day headers
        day = normalize_day_token(line)
        if day:
            current_day = day
            block_menus.setdefault(current_day, [])
            continue

        # Skip newsletter/boilerplate noise lines we saw on the pages
        if re.search(r"Sign up for our newsletter|Tilmeld dig vores nyhedsbrev|Opening hours|Åbningstider", line, flags=re.I):
            continue

        # Capture vegetarian labeling consistently
        is_veg_label = bool(re.search(r"^\s*(vegetar|vegetarian)\s*:?\s*$", line, flags=re.I))
        if is_veg_label:
            # push as a label; the next line usually holds the veg dish
            # We'll render with upper-case in get_today_menus like you did
            if current_day:
                block_menus[current_day].append(line.rstrip(":").capitalize() + ":")
            else:
                collected_common.append(line.rstrip(":").capitalize() + ":")
            continue

        # Otherwise, treat as a menu item
        if current_day:
            block_menus[current_day].append(line)
        else:
            collected_common.append(line)

    # If a page only lists items without day headers, spread them across all days
    if not block_menus and collected_common:
        block_menus = { d: collected_common[:] for d in DAILY_DAYS_DA }
    else:
        # Ensure every weekday exists (fill with common lines)
        for d in DAILY_DAYS_DA:
            block_menus.setdefault(d, [])
            for item in collected_common:
                if item not in block_menus[d]:
                    block_menus[d].append(item)

    # Deduplicate per day
    for d in list(block_menus.keys()):
        seen = set()
        dedup = []
        for it in block_menus[d]:
            norm = " ".join(it.split())
            if norm and norm not in seen:
                seen.add(norm)
                dedup.append(it)
        block_menus[d] = dedup

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
            # accept exact word or heading that contains it
            if re.search(rf"\b{re.escape(r)}\b", low):
                found_rest = r.capitalize()
                break
        if found_rest:
            current_restaurant = found_rest
            current_days = []
            ensure_rest_day_entries(current_restaurant, DAILY_DAYS_DA)  # pre-create weekdays
            continue

        # Detect days for multi-day lines (e.g., "Monday/Tuesday ...")
        candidate = low.replace(":", " ").replace("/", " ").replace(",", " ")
        found_days = []
        for k in VALID_DAYS:
            if k in candidate:
                found_days.append(DAY_MAP[k])
        if found_days and current_restaurant:
            current_days = sorted(set(found_days))
            ensure_rest_day_entries(current_restaurant, current_days)
            continue

        # Sprout: keep the primary describe-the-salad line only
        if current_restaurant == "Sprout":
            # Keep lines that describe the daily salad; skip noise
            if re.search(r"(salad|salat|protein|dagens)", low):
                sprout_buffer.append(line)
            # Ignore other lines under Sprout
            continue

        # Vegetarian label (keep as-is if present)
        if re.search(r"(vegetar|vegetarian)\s*:", low) and current_restaurant:
            days_target = current_days if current_days else DAILY_DAYS_DA
            for d in days_target:
                restaurant_menus[current_restaurant][d].append(line)
            continue

        # Normal menu items
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
            seen = set()
            dedup = []
            for it in items:
                norm = " ".join(it.split())
                if norm and norm not in seen:
                    seen.add(norm)
                    dedup.append(it)
            days[d] = dedup

    return restaurant_menus

    def ensure_rest_day_entries(name, days):
        restaurant_menus.setdefault(name, {})
        for d in days:
            restaurant_menus[name].setdefault(d, [])

    for line in lines:
        low = line.lower()

        # Detect restaurant change
        found_rest = None
        for r in RESTAURANTS_FOODCOURT:
            if r in low:
                found_rest = r.capitalize()
                break
        if found_rest:
            current_restaurant = found_rest
            current_days = []
            ensure_rest_day_entries(current_restaurant, DAILY_DAYS_DA)  # pre-create weekdays
            continue

        # Detect days that apply to the next menu lines (e.g., "Monday/Tuesday ...")
        candidate = low.replace(":", " ").replace("/", " ").replace(",", " ")
        found_days = []
        for k in VALID_DAYS:
            if k in candidate:
                found_days.append(DAY_MAP[k])
        if found_days and current_restaurant:
            current_days = sorted(set(found_days))
            ensure_rest_day_entries(current_restaurant, current_days)
            continue

        # Sprout is described as a daily option → collect buffer to assign to all days
        if current_restaurant == "Sprout":
            sprout_buffer.append(line)
            continue

        # Vegetarian label
        if re.search(r"(vegetar|vegetarian)\s*:", low):
            if current_restaurant and current_days:
                for d in current_days:
                    restaurant_menus[current_restaurant][d].append(line)
            continue

        # Normal menu items
        if current_restaurant and line and not any(r in low for r in RESTAURANTS_FOODCOURT):
            days_target = current_days if current_days else DAILY_DAYS_DA
            for d in days_target:
                restaurant_menus[current_restaurant][d].append(line)

    # Assign Sprout to all days
    if "Sprout" in restaurant_menus:
        for d in DAILY_DAYS_DA:
            restaurant_menus["Sprout"][d] = sprout_buffer[:]

    # Dedup per restaurant/day
    for rname, days in restaurant_menus.items():
        for d, items in days.items():
            seen = set()
            dedup = []
            for it in items:
                norm = " ".join(it.split())
                if norm and norm not in seen:
                    seen.add(norm)
                    dedup.append(it)
            days[d] = dedup

    return restaurant_menus

# --- End-to-end scraping ---

def scrape_weekly_menus():
    """Scrape HUB1, HUB2, HUB3, and Foodcourt into a single dict."""
    menus_by_hub = {}

    # Regular hubs
    for hub_name, urls in HUB_PAGES.items():
        html = fetch_page(urls)
        if not html:
            print(f"Warning: could not load any URL for {hub_name}: {urls}")
            continue

        if hub_name == "FOODCOURT":
            fc_menus = parse_foodcourt_page(html)
            # Add each restaurant as its own 'hub' key (to match your existing downstream logic)
            for rname, days in fc_menus.items():
                menus_by_hub[rname] = days
        else:
            block_menus = parse_hub_page(html)
            menus_by_hub[hub_name] = block_menus

    print("Scraped menus by hub:", menus_by_hub)
    return menus_by_hub

# --- Today's menus & RSS (mostly kept from your code) ---


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

        # Filter and dedupe lines for the day
        seen = set()
        unique_menu = []
        for item in items_today:
            normalized = tidy_line(" ".join(item.split()).replace("\u200d", ""))
            if not normalized:
                continue
            skip_days = ["mandag","tirsdag","onsdag","torsdag","fredag","monday","tuesday","wednesday","thursday","friday"]
            if any(day in normalized.lower() for day in skip_days):
                continue
            if is_boilerplate(normalized):
                continue
            if normalized not in seen:
                seen.add(normalized)
                # Keep original-cased line but tidied for final display
                unique_menu.append(tidy_line(item))

        # If nothing meaningful remains, skip the hub
        if not unique_menu:
            continue

        # Build formatted menu once per hub
        formatted_menu = []
        i = 0
        while i < len(unique_menu):
            line = unique_menu[i].strip()
            lower_line = line.lower().rstrip(" ")

            # HUB1 – Kays vegetarian label on same line
            if hub == "HUB1 – Kays" and (lower_line.startswith("vegetar:") or lower_line.startswith("vegetarian:")):
                parts = line.split(":", 1)
                if len(parts) > 1 and parts[1].strip():
                    caps_start = f"{parts[0].strip().upper()}: {parts[1].strip()}"
                    formatted_menu.append(caps_start)
                    formatted_menu.append(" | ")
                else:
                    formatted_menu.append(tidy_line(f"   {line} | "))

            # Standalone vegetarian labels
            elif lower_line in ["vegetar", "vegetarian", "vegetar:", "vegetarian:"]:
                if not line.endswith(":"):
                    line += ":"
                line = f"{line.rstrip(':').upper()}:"

                if hub in ["HUB2", "HUB3"]:
                    formatted_menu.append(line.upper())
                    if i + 1 < len(unique_menu):
                        formatted_menu.append(f"   {unique_menu[i+1].strip()}")
                        i += 1
                    formatted_menu.append("")
                elif hub == "Globetrotter":
                    formatted_menu.append("")
                    formatted_menu.append(line)
            else:
                formatted_menu.append(f"   {line} | ")

            i += 1

        menu_text = "\n".join(tidy_line(x) for x in formatted_menu if tidy_line(x))

        # Append once per hub
        if hub in ["Homebound", "Globetrotter", "Sprout"]:
            today_menus.append(f"🍽 HUB1 - {hub} Lunch Menu:  | \n{menu_text}")
        else:
            today_menus.append(f"🍽 {hub} - Lunch Menu:  | \n{menu_text}")

    return today_menus




from hashlib import sha1

def summarize_title(item: str) -> str:
    # First line up to colon or whole prefix before body
    title = item.split(":\n", 1)[0].strip()
    if not title:
        title = item.split(":", 1)[0].strip()
    return title if title else "Today's Menu"

def long_body(item: str) -> str:
    # Full formatted body lines, tidied and without boilerplate
    lines = [tidy_line(ln) for ln in item.split("\n")]
    lines = [ln for ln in lines if ln and not is_boilerplate(ln)]
    return "\n".join(lines)

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
        # Example:
        # 🍽 HUB1 – Kays - Lunch Menu
        #   Game stew ...
        #   VEGETARIAN: ...
        description_full = f"{title_text}\n{body_text}".strip()

        entry = fg.add_entry()
        entry.title(title_text)
        entry.link(href="https://hubnordic.madkastel.dk/en/menu")
        entry.description(description_full)   # <-- put everything in description (plain text)
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
   


if __name__ == "__main__":
    menus_by_hub = scrape_weekly_menus()
    today_menus = get_today_menus(menus_by_hub)
    today_menus = dedupe_items(today_menus)
    print("Today's menu:")
    for menu in today_menus:
        print(menu, end="\n\n")
    generate_rss(today_menus)
