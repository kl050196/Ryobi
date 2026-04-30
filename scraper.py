import os
import json
import time
import requests
from playwright.sync_api import sync_playwright

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FB_EMAIL = os.environ.get("FB_EMAIL", "")
FB_PASSWORD = os.environ.get("FB_PASSWORD", "")

SEARCH_QUERY = "Ryobi 36V"
CITY = "sydney"
SEEN_FILE = "seen.json"

MARKETPLACE_URL = (
    f"https://www.facebook.com/marketplace/{CITY}/search"
    f"?query={SEARCH_QUERY.replace(' ', '%20')}&sortBy=creation_time_descend"
)
# ─────────────────────────────────────────────────────────────────────────────


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print(f"  ✅ Telegram alert sent")
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")


def login_facebook(page):
    print("🔐 Logging into Facebook...")
    page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
    time.sleep(2)

    # Accept cookies if prompted
    try:
        page.click('button[data-cookiebanner="accept_button"]', timeout=4000)
        time.sleep(1)
    except:
        pass

    page.fill('#email', FB_EMAIL)
    page.fill('#pass', FB_PASSWORD)
    page.click('[name="login"]')
    page.wait_for_load_state("networkidle")
    time.sleep(3)
    print("  ✅ Logged in")


def scrape_listings(page):
    print(f"🔍 Searching: {SEARCH_QUERY} in {CITY.title()}...")
    page.goto(MARKETPLACE_URL, wait_until="domcontentloaded")
    time.sleep(4)

    # Scroll to load more listings
    for _ in range(3):
        page.evaluate("window.scrollBy(0, 800)")
        time.sleep(1.5)

    # Extract listing cards
    listings = page.evaluate("""
        () => {
            const results = [];
            // Facebook marketplace listing links
            const links = document.querySelectorAll('a[href*="/marketplace/item/"]');
            const seen_hrefs = new Set();

            links.forEach(link => {
                const href = link.href;
                if (seen_hrefs.has(href)) return;
                seen_hrefs.add(href);

                // Get listing ID from URL
                const match = href.match(/item\\/([0-9]+)/);
                if (!match) return;
                const id = match[1];

                // Try to get title and price from nearby text
                const container = link.closest('[style]') || link.parentElement;
                const allText = container ? container.innerText : '';
                const lines = allText.split('\\n').map(s => s.trim()).filter(Boolean);

                // Price is usually a line starting with $
                const priceLine = lines.find(l => l.startsWith('$')) || 'Price not listed';
                // Title is usually the longest line
                const title = lines.sort((a, b) => b.length - a.length)[0] || 'Ryobi 36V item';

                results.push({ id, title, price: priceLine, url: href });
            });

            return results;
        }
    """)

    print(f"  Found {len(listings)} listings on page")
    return listings


def run():
    print("=" * 50)
    print(f"🤖 Ryobi 36V Bot — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    seen = load_seen()
    new_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            login_facebook(page)
            listings = scrape_listings(page)

            for item in listings:
                if item["id"] in seen:
                    print(f"  ⏭  Already seen: {item['id']}")
                    continue

                print(f"  🆕 New listing: {item['title']} — {item['price']}")
                msg = (
                    f"🔧 *New Ryobi 36V Listing!*\n\n"
                    f"*{item['title']}*\n"
                    f"💰 {item['price']}\n"
                    f"📍 Sydney, NSW\n"
                    f"🔗 {item['url']}"
                )
                send_telegram(msg)
                seen.add(item["id"])
                new_count += 1
                time.sleep(1)  # polite delay between messages

            save_seen(seen)

        except Exception as e:
            print(f"❌ Error: {e}")
            send_telegram(f"⚠️ Ryobi bot error: {e}")
        finally:
            browser.close()

    print(f"\n✅ Done. {new_count} new listing(s) found.")
    print("=" * 50)


if __name__ == "__main__":
    run()
