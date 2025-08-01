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

MENU_URL = "https://hubnordic.madkastel.dk/"
FEED_URL = "https://arctoz00.github.io/canteen-rss-feed/feed.xml"  # Opdater til din faktiske feed-URL
RSS_FILE = "feed.xml"

def get_rendered_html():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.implicitly_wait(30)
    
    driver.get(MENU_URL)
    try:
        wait = WebDriverWait(driver, 120)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.et_pb_text_inner")))
    except Exception as e:
        print("Timed out waiting for content to load:", e)
    time.sleep(2)
    html = driver.page_source
    driver.quit()
    return html

def scrape_weekly_menus():
    
    html = get_rendered_html()
    soup = BeautifulSoup(html, "html.parser")
    
    hub_divs = soup.find_all("div", class_="et_pb_text_inner")
    menus_by_hub = {}
    
    valid_days = ['mandag','tirsdag','onsdag','torsdag','fredag','lørdag','søndag']
    daily_days = ['mandag','tirsdag','onsdag','torsdag','fredag']
    
    for div in hub_divs:
        header = div.find("h4")
        if not header:
            continue
        
        raw_header = header.get_text(separator=" ", strip=True)
        lower_header = raw_header.lower()
        
        if "hub1" in lower_header:
            if "verdenskøkken" in lower_header:
                hub_name = "HUB1 – Kays Verdenskøkken"
            else:
                hub_name = "HUB1 – Kays"
        elif "hub2" in lower_header or "hu b2" in lower_header:
            hub_name = "HUB2"
        elif "hub 3" in lower_header:
            hub_name = "HUB3"
        else:
            continue
        
        if hub_name == "HUB1 – Kays Verdenskøkken":
            hub_daily_days = ['mandag', 'tirsdag', 'torsdag', 'fredag']
        else:
            hub_daily_days = daily_days
        
        block_menus = {}
        current_day = None
        collected_items = []
        
        for p in div.find_all("p"):
            text = p.get_text(separator=" ", strip=True)
            candidate = text.replace(":", "").strip().lower()
            if candidate in valid_days:
                current_day = candidate
                if current_day not in block_menus:
                    block_menus[current_day] = []
            else:
                if "globetrotter menu" in candidate or "vegetar" in candidate:
                    if text not in collected_items:
                        collected_items.append(text)
                else:
                    if current_day:
                        block_menus[current_day].append(text)
                    else:
                        if text not in collected_items:
                            collected_items.append(text)
        
        if not block_menus and collected_items:
            block_menus = { day: collected_items.copy() for day in hub_daily_days }
        else:
            for d in hub_daily_days:
                if d not in block_menus:
                    block_menus[d] = collected_items.copy()
                else:
                    for item in collected_items:
                        if item not in block_menus[d]:
                            block_menus[d].append(item)
        
        if hub_name in menus_by_hub:
            for day, items in block_menus.items():
                if day in menus_by_hub[hub_name]:
                    menus_by_hub[hub_name][day].extend(items)
                else:
                    menus_by_hub[hub_name][day] = items
        else:
            menus_by_hub[hub_name] = block_menus

    for hub in menus_by_hub:
        for day in menus_by_hub[hub]:
            menus_by_hub[hub][day] = list(dict.fromkeys(menus_by_hub[hub][day]))
    
    return menus_by_hub

def get_today_menus(menus_by_hub):
    weekday_mapping = {
        "Monday": "mandag",
        "Tuesday": "tirsdag",
        "Wednesday": "onsdag",
        "Thursday": "torsdag",
        "Friday": "fredag",
        "Saturday": "lørdag",
        "Sunday": "søndag"
    }
    today_en = datetime.datetime.today().strftime("%A")
    today_da = weekday_mapping.get(today_en, "").lower()
    
    print("Systemets dag (engelsk):", today_en)
    print("Mapper til (dansk):", today_da)
    
    ønskede_hubs = {"HUB1 – Kays", "HUB1 – Kays Verdenskøkken", "HUB2", "HUB3"}
    today_menus = []
    
    for hub, menu_dict in menus_by_hub.items():
        if hub not in ønskede_hubs:
            continue
        if today_da in menu_dict and menu_dict[today_da]:
            seen = set()
            unique_menu = []
            for item in menu_dict[today_da]:
                normalized = " ".join(item.split())
                if normalized not in seen:
                    seen.add(normalized)
                    unique_menu.append(item)
            menu_text = " | ".join(unique_menu).replace("\n", " ").strip()
            today_menus.append(f"{hub}: {menu_text}")
    return today_menus

def generate_rss(menu_items):
    fg = FeedGenerator()
    today_str = datetime.date.today().strftime("%A, %d %B %Y")
    
    fg.title(f"Canteen Menu - {today_str}")
    fg.link(href=MENU_URL)
    fg.description("Dagligt opdateret canteen-menu")
    fg.language("da")
    fg.lastBuildDate(datetime.datetime.now(pytz.utc))
    fg.generator("Python feedgen")
    fg.ttl(15)
    fg.docs("http://www.rssboard.org/rss-specification")
    
    for i, item in enumerate(menu_items):
        entry = fg.add_entry()
        entry.title(f"<![CDATA[{item}]]>")
        entry.link(href=MENU_URL)
        entry.description(f"<![CDATA[{item}]]>")
        entry.pubDate(datetime.datetime.now(pytz.utc))
        clean_item = re.sub(r"\s+", "", item).lower()
        guid_value = f"urn:canteen:{clean_item}-{datetime.datetime.now().strftime('%Y%m%d')}-{i}"
        entry.guid(guid_value)
    
    rss_bytes = fg.rss_str(pretty=True)
    rss_str = rss_bytes.decode("utf-8")
    
    atom_link_str = f'    <atom:link href="{FEED_URL}" rel="self" type="application/rss+xml"/>\n'
    rss_str = rss_str.replace("<channel>", "<channel>\n" + atom_link_str, 1)
    
    docs_str = '    <docs>http://www.rssboard.org/rss-specification</docs>\n'
    rss_str = rss_str.replace(atom_link_str, atom_link_str + docs_str, 1)
    
    rss_str = rss_str.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")
    
    rss_str = re.sub(r'<guid>(.*?)</guid>', r'<guid isPermaLink="false"><![CDATA[\1]]></guid>', rss_str)
    
    rss_str = re.sub(r'<title>(.*?)</title>', r'<title><![CDATA[\1]]></title>', rss_str, count=1)
    rss_str = re.sub(r'<description>(.*?)</description>', r'<description><![CDATA[\1]]></description>', rss_str, count=1)
    
    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(rss_str)
    print("RSS feed opdateret")

if __name__ == "__main__":
    menus_by_hub = scrape_weekly_menus()
    today_menus = get_today_menus(menus_by_hub)
    print("Dagens menuer:")
    for menu in today_menus:
        print(menu)
    generate_rss(today_menus)

