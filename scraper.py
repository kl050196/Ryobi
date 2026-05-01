import os
import json
import time
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
FB_EMAIL         = os.environ.get("FB_EMAIL", "")
FB_PASSWORD      = os.environ.get("FB_PASSWORD", "")

SEARCH_QUERY = "Ryobi 36V"
CITY         = "sydney"
SEEN_FILE    = "seen.json"

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
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️  Telegram credentials missing — check environment variables")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Escape special Markdown chars that break Telegram
    safe_message = message.replace("_", "\\_").replace("[", "\\[").replace("`", "\\`")
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": safe_message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("  ✅ Telegram alert sent")
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")


def dismiss_popups(page):
    """Try to dismiss cookie banners and login prompts."""
    # Cookie accept button
    for selector in [
        'button[data-cookiebanner="accept_button"]',
        '[aria-label="Allow all cookies"]',
        '[aria-label="Accept all"]',
    ]:
        try:
            page.click(selector, timeout=3000)
            time.sleep(1)
            break
        except Exception:
            pass

    # Close any login / sign-up modal that blocks marketplace
    for selector in [
        '[aria-label="Close"]',
        '[role="dialog"] [aria-label="Close"]',
    ]:
        try:
            page.click(selector, timeout=2000)
            time.sleep(1)
            break
        except Exception:
            pass


def login_facebook(page):
    print("🔐 Logging into Facebook...")
    page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)

    dismiss_popups(page)

    # Fill credentials
    page.fill('#email', FB_EMAIL)
    time.sleep(0.5)
    page.fill('#pass', FB_PASSWORD)
    time.sleep(0.5)
    page.click('[name="login"]')

    # Wait for redirect away from login page
    try:
        page.wait_for_url(lambda url: "login" not in url, timeout=15000)
    except PlaywrightTimeoutError:
        print("  ⚠️  Login may have failed — check FB_EMAIL and FB_PASSWORD variables")
        raise Exception("Facebook login failed — still on login page after 15s")

    time.sleep(2)
    print("  ✅ Logged in")


def scrape_listings(page):
    print(f"🔍 Searching: {SEARCH_QUERY} in {CITY.title()}...")
    page.goto(MARKETPLACE_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)

    dismiss_popups(page)

    # Scroll to load more listings
    for _ in range(4):
        page.evaluate("window.scrollBy(0, 900)")
        time.sleep(1.5)

    # Extract listing cards
    listings = page.evaluate(r"""
        () => {
            const results = [];
            const links = document.querySelectorAll('a[href*="/marketplace/item/"]');
            const seenHrefs = new Set();

            links.forEach(link => {
                const href = link.href.split('?')[0]; // strip query params for clean URL
                if (seenHrefs.has(href)) return;
                seenHrefs.add(href);

                const match = href.match(/item\/([0-9]+)/);
                if (!match) return;
                const id = match[1];

                // Walk up the DOM to find a container with useful text
                let container = link;
                for (let i = 0; i < 5; i++) {
                    if (container.parentElement) container = container.parentElement;
                    if (container.innerText && container.innerText.length > 10) break;
                }

                const allText = container ? container.innerText : '';
                const lines = allText.split('\n').map(s => s.trim()).filter(Boolean);

                const priceLine = lines.find(l => l.match(/^\$[\d,]+/)) || 'Price not listed';
                // Filter out very short lines and pick the most descriptive one
                const titleLine = lines
                    .filter(l => l.length > 5 && !l.match(/^\$/) && !l.match(/^\d+ (min|hr|day)/))
                    .sort((a, b) => b.length - a.length)[0] || 'Ryobi 36V item';

                results.push({ id, title: titleLine, price: priceLine, url: href });
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

    if not FB_EMAIL or not FB_PASSWORD:
        print("❌ FB_EMAIL or FB_PASSWORD environment variable is missing!")
        send_telegram("⚠️ Ryobi bot error: Facebook credentials not set in Railway variables.")
        return

    seen = load_seen()
    new_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-AU",
        )
        page = context.new_page()

        try:
            login_facebook(page)
            listings = scrape_listings(page)

            if not listings:
                print("  ⚠️  No listings found — Facebook may have blocked the request or changed layout")

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
                time.sleep(1.5)

            save_seen(seen)

        except Exception as e:
            print(f"❌ Error during run: {e}")
            send_telegram(f"⚠️ Ryobi bot error: {e}")
        finally:
            browser.close()

    print(f"\n✅ Done. {new_count} new listing(s) found.")
    print("=" * 50)


if __name__ == "__main__":
    run()
