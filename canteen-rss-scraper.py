import time #date/time
import datetime #date/time
import pytz #timezone
import re #regex
from selenium import webdriver #automated browser interaction
from selenium.webdriver.chrome.options import Options #chrome options for headless mode (so it doesn't open a browser window)
from selenium.webdriver.common.by import By #element selection
from selenium.webdriver.support.ui import WebDriverWait #wait for elements to load
from selenium.webdriver.support import expected_conditions as EC #expected conditions for elements
from bs4 import BeautifulSoup #HTML parsing
from feedgen.feed import FeedGenerator  #RSS feed generation

#URL to the canteen menu page
MENU_URL = "https://hubnordic.madkastel.dk/"  
#URL for the RSS feed  
FEED_URL = "https://denmarksynergy-creator.github.io/Canteen_feed/feed.xml"
#Local file in repo to save the RSS feed
RSS_FILE = "feed.xml"


def get_rendered_html():
    '''Fetches the rendered HTML of the canteen menu page using Selenium.
    This is necessary because the page content is dynamically loaded with JavaScript.'''

    # Set up Chrome options for headless mode
    # Headless mode allows the browser to run in the background without opening a window
    chrome_options = Options() 
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # Initialize the Chrome driver
    driver = webdriver.Chrome(options=chrome_options) 
    driver.implicitly_wait(30) # Wait for elements to load
    
    # Navigate to the canteen menu page
    driver.get(MENU_URL) 
    try:
        wait = WebDriverWait(driver, 120)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div.et_pb_text_inner")))
    except Exception as e:
        print("Timed out waiting for content to load:", e)
    time.sleep(2)
    html = driver.page_source # Get the page source after it has been fully rendered
    driver.quit()
    return html # Return the rendered HTML content for further processing

def scrape_weekly_menus():
    '''Scrapes the weekly menus from the canteen page and organizes them by hub and day.
    Returns a dictionary with hub names as keys and another dictionary of days and menu items as values.
    Each day's menu is a list of strings representing the menu items.'''
    
    html = get_rendered_html()
    soup = BeautifulSoup(html, "html.parser") # Parse the HTML content with BeautifulSoup library

    # Find all divs with class "et_pb_text_inner" which contain the menu information
    hub_divs = soup.find_all("div", class_="et_pb_text_inner")
    menus_by_hub = {} # Dictionary to hold menus organized by hub and day
    
    valid_days = ['mandag','tirsdag','onsdag','torsdag','fredag','monday','tuesday','wednesday','thursday','friday']
    daily_days = ['mandag','tirsdag','onsdag','torsdag','fredag']
    
    # Iterate through each div, note it's the same as the "et_pb_text_inner" class, which contains not just hubs
    for div in hub_divs: 
        header = div.find("h4") # Find the header of the hub section that has the "h4" tag
        if not header:
            header = div.find("strong") # Some sections might not have an "h4" tag, so we check for "FOODCORE"
        if not header:
            continue
        
        # Get the text of the header and convert it to lowercase for easier matching
        lower_header = header.get_text(separator=" ", strip=True).lower() 
        
        # Determine the hub name based on the header text
        if "hub 1" in lower_header:
            hub_name = "HUB1 – Kays"
        elif "hub 2" in lower_header or "hu b2" in lower_header:
            hub_name = "HUB2"
        elif "hub 3" in lower_header:
            hub_name = "HUB3"
        elif "foodcore" in lower_header:
            hub_name = "HUB1 – Foodcort"
        else:
            continue

        # Special handling for the Foodcort hub   
        if hub_name == "HUB1 – Foodcort": 
            restaurant_menus = {}
            current_restaurant = None
            current_days = []
            days_map = {
                "mandag": "mandag", "tirsdag": "tirsdag", "onsdag": "onsdag", "torsdag": "torsdag", "fredag": "fredag",
                "monday": "mandag", "tuesday": "tirsdag", "wednesday": "onsdag", "thursday": "torsdag", "friday": "fredag"
            }
            valid_days = set(days_map.values())
            restaurant_names = ["globetrotter", "homebound", "sprout"]
            sprout_buffer = [] # Buffer for Sprout menu items
            for p in div.find_all("p"):
                text = p.get_text(separator=" ", strip=True)
                lower_text = text.lower()
                # Detect restaurant header
                found_restaurant = None
                for rname in restaurant_names:
                    if rname in lower_text:
                        found_restaurant = rname.capitalize()
                        break
                if found_restaurant:
                    current_restaurant = found_restaurant
                    if current_restaurant not in restaurant_menus:
                        restaurant_menus[current_restaurant] = {}
                    current_days = []
                    continue
                # Detect days (can be multiple)
                candidate = text.replace(":", "").replace("/", " ").replace(",", " ").lower()
                found_days = [d for d in valid_days if d in candidate]
                if found_days and current_restaurant:
                    current_days = found_days
                    for d in current_days:
                        if d not in restaurant_menus[current_restaurant]:
                            restaurant_menus[current_restaurant][d] = []
                    continue
                # Sprout special handling (no days, just buffer all lines)
                if current_restaurant == "Sprout":
                    if text:
                        sprout_buffer.append(text)
                    continue
                # Detect vegetarian
                if "vegetar" in lower_text or "vegetarian" in lower_text:
                    for d in current_days:
                        restaurant_menus[current_restaurant][d].append(text)
                    continue
                # Add menu items
                if current_days and current_restaurant and text and not any(rn in lower_text for rn in restaurant_names):
                    for d in current_days:
                        restaurant_menus[current_restaurant][d].append(text)
            # Assign Sprout menu to all days
            if "Sprout" in restaurant_menus:
                for d in daily_days:
                    restaurant_menus["Sprout"][d] = sprout_buffer.copy()
            # Add each restaurant as a hub
            for rname, days in restaurant_menus.items():
                menus_by_hub[rname] = days
            print("Scraped Foodcort menus:", restaurant_menus,end="\n\n\n")
            continue  # Skip the rest of the hub parsing for Foodcort
        else:
            day_map = {
                "monday": "mandag", "tuesday": "tirsdag", "wednesday": "onsdag",
                "thursday": "torsdag", "friday": "fredag",
                "mandag": "mandag", "tirsdag": "tirsdag", "onsdag": "onsdag",
                "torsdag": "torsdag", "fredag": "fredag"
            }
            hub_daily_days = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag"]
            block_menus = {}
            current_day = None
            collected_items = []

            for p in div.find_all("p"):
                text = p.get_text(separator=" ", strip=True)
                candidate = text.replace(":", "").strip().lower()
                # Check if the text matches any valid day (English or Danish)
                if candidate in day_map:
                    current_day = day_map[candidate]
                    if current_day not in block_menus:
                        block_menus[current_day] = []
                else:
                    if "vegeterian" in candidate or "vegetar" in candidate:
                        if current_day:
                            block_menus[current_day].append(text)
                        else:
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
    
    print("Scraped menus by hub:", menus_by_hub)
    return menus_by_hub 

def get_today_menus(menus_by_hub):
    '''Returns today's menus based on the current day of the week.
    It maps the English weekday to Danish and retrieves the menus for that day from the provided dictionary
    of menus organized by hub and restaurant.'''

    weekday_mapping = {
        "Monday": "mandag",
        "Tuesday": "tirsdag",
        "Wednesday": "onsdag",
        "Thursday": "torsdag",
        "Friday": "fredag",
        "Saturday": "lørdag",
        "Sunday": "søndag"
    }
    today_en = datetime.datetime.today().strftime("%A") # Get the current day of the week in English
    today_da = weekday_mapping.get(today_en, "").lower() # Map it to Danish and convert to lowercase
    
    print("Systemets dag (engelsk):", today_en)
    print("Mapper til (dansk):", today_da)
    
    target_hubs = {"HUB1 – Kays", "Homebound", "HUB2", "HUB3", "Globetrotter", "Sprout"}
    today_menus = []
    
    # Iterate through the menus organized by hub and collect today's menus
    for hub, menu_dict in menus_by_hub.items(): 
        if hub not in target_hubs:
            continue
        if today_da in menu_dict and menu_dict[today_da]:
            seen = set()
            unique_menu = []
            for item in menu_dict[today_da]:
                normalized = " ".join(item.split())
                # Skip empty strings
                if not normalized:
                    continue
                # Skip if the string contains any weekday (Danish or English, Mon-Fri)
                skip_days = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "monday", "tuesday", "wednesday", "thursday", "friday"]
                if any(day in normalized.lower() for day in skip_days):
                    continue
                if normalized not in seen:
                    seen.add(normalized)
                    unique_menu.append(item)
                '''normalized = " ".join(item.split())
                if normalized not in seen:
                    seen.add(normalized)
                    unique_menu.append(item)'''
            #menu_text = " | ".join(unique_menu).replace("\n", " ").strip()
            #menu_text = "\n".join(f"• {line.strip()}" for line in unique_menu)

            #print(f"This is the unique menu: ", unique_menu)

            formatted_menu = []
            i = 0

            while i < len(unique_menu):
                line = unique_menu[i].strip()
                lower_line = line.lower().rstrip(" ")

                # HUB1 - Kays special: Vegetar: + other text on same line
                if hub == "HUB1 – Kays" and (lower_line.startswith("vegetar:") or lower_line.startswith("vegetarian:")):
                    parts = line.split(":", 1)
                    if len(parts) > 1 and parts[1].strip():  # Has more text
                        caps_start = f"{parts[0].strip().upper()}: {parts[1].strip()}"
                        formatted_menu.append(caps_start)
                        formatted_menu.append(" | ")  # Use a visible separator instead of a line break
                    else:
                        formatted_menu.append(f"   {line}")

                # Standalone vegetarians (just the word or word+colon)
                elif lower_line in ["vegetar", "vegetarian", "vegetar:", "vegetarian:"]:
                    # Ensure colon
                    if not line.endswith(":"):
                        line += ":"
                    # Bold the whole thing
                    line = f"{line.rstrip(':').upper()}:"

                    if hub in ["HUB2", "HUB3"]:  
                        # HUB2/3 → add veg line, then next line, then break
                        formatted_menu.append(line)
                        if i + 1 < len(unique_menu):
                            formatted_menu.append(f"   {unique_menu[i+1].strip().upper()}")
                            i += 1  # skip next line since we already processed
                        formatted_menu.append(" | ")  # break after the next line

                    elif hub == "Globetrotter":  
                        # Globetrotter → break before veg line
                        formatted_menu.append("")  
                        formatted_menu.append(line)

                else:
                    formatted_menu.append(f"   {line} | ")

                i += 1                


            menu_text = "\n".join(formatted_menu)


            
            if hub in ["Homebound", "Globetrotter", "Sprout"]:
                today_menus.append(f"🍽 HUB1 - {hub} Lunch Menu:  | \n{menu_text}")
            else:
                today_menus.append(f"🍽 {hub} - Lunch Menu:  | \n{menu_text}")
    return today_menus

def generate_rss(menu_items):
    '''Generates an RSS feed from the provided menu items.
    Each menu item is added as an entry in the feed with appropriate metadata.'''

    fg = FeedGenerator() # Create a new FeedGenerator instance
    today_str = datetime.date.today().strftime("%A, %d %B %Y") # Get today's date in a readable format
    
    # Set up the feed metadata
    fg.title(f"Canteen Menu - {today_str}") 
    fg.link(href=MENU_URL)
    fg.description("Daily updated canteen-menu")
    fg.language("da")
    fg.lastBuildDate(datetime.datetime.now(pytz.utc))
    fg.generator("Python feedgen")
    fg.ttl(15)
    fg.docs("http://www.rssboard.org/rss-specification")
    
    # Fill the feed with menu items 
    for i, item in enumerate(menu_items): 
        entry = fg.add_entry() # Add a new entry to the feed
        #entry.title(f"<![CDATA[{item}]]>") # Use CDATA to allow special characters in the title
        short_title = item.split(":")[0].strip()
        if not short_title:
            short_title = item[:50] + "..." if len(item) > 50 else item
        entry.title(short_title)  # Short, SmartSign-friendly title
        entry.link(href=MENU_URL)  # Link to the canteen menu page
        entry.description(f"<![CDATA[{item}]]>")   # Use CDATA for the description to allow special characters
        entry.pubDate(datetime.datetime.now(pytz.utc)) 
        clean_item = re.sub(r"\s+", "", item).lower()
        guid_value = f"urn:canteen:{clean_item}-{datetime.datetime.now().strftime('%Y%m%d')}-{i}" 
        entry.guid(guid_value)
    
    rss_bytes = fg.rss_str(pretty=True) # Generate the RSS feed as bytes
    rss_str = rss_bytes.decode("utf-8") # Decode bytes to string
    
    # Add the atom link and docs to the RSS feed
    atom_link_str = f'    <atom:link href="{FEED_URL}" rel="self" type="application/rss+xml"/>\n'
    rss_str = rss_str.replace("<channel>", "<channel>\n" + atom_link_str, 1)
    
    # Add the docs link to the RSS feed
    docs_str = '    <docs>http://www.rssboard.org/rss-specification</docs>\n'
    rss_str = rss_str.replace(atom_link_str, atom_link_str + docs_str, 1)
    
    # Replace CDATA tags with proper syntax
    rss_str = rss_str.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")
    
    # Ensure guid is not a permalink and wrap it in CDATA
    rss_str = re.sub(r'<guid>(.*?)</guid>', r'<guid isPermaLink="false"><![CDATA[\1]]></guid>', rss_str)
    
    # Wrap title and description in CDATA to handle special characters
    # Only wrap the first occurrence of title and description to avoid double wrapping
    rss_str = re.sub(r'<title>(.*?)</title>', r'<title><![CDATA[\1]]></title>', rss_str, count=1)
    rss_str = re.sub(r'<description>(.*?)</description>', r'<description><![CDATA[\1]]></description>', rss_str, count=1)
    
    # Write the RSS feed to a file
    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(rss_str)
    print("RSS feed updated")

if __name__ == "__main__":
    menus_by_hub = scrape_weekly_menus()
    today_menus = get_today_menus(menus_by_hub)
    print("Today's menu:")
    for menu in today_menus:
        print(menu, end="\n\n")
    generate_rss(today_menus)

