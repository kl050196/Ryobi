import os
import json
import time
import base64
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
        print("  ⚠️  Telegram credentials missing")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
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


def send_telegram_photo(image_bytes):
    """Send a screenshot to Telegram for debugging."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID}, files={"photo": ("screenshot.png", image_bytes, "image/png")}, timeout=20)
        r.raise_for_status()
        print("  📸 Screenshot sent to Telegram")
    except Exception as e:
        print(f"  ❌ Screenshot send error: {e}")


def accept_cookies(page):
    cookie_selectors = [
        'button[data-cookiebanner="accept_button"]',
        '[aria-label="Allow all cookies"]',
        '[aria-label="Accept all"]',
        'button:has-text("Accept All")',
        'button:has-text("Allow all cookies")',
        'button:has-text("Allow All")',
        'button:has-text("OK")',
    ]
    for selector in cookie_selectors:
        try:
            page.click(selector, timeout=3000)
            print(f"  ✅ Cookie banner dismissed")
            time.sleep(1.5)
            return
        except Exception:
            pass


def login_facebook(page):
    print("🔐 Logging into Facebook...")

    page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    accept_cookies(page)
    time.sleep(1)

    # Take a screenshot so we can see what Facebook is showing
    print("  📸 Taking screenshot of current page...")
    screenshot = page.screenshot()
    send_telegram_photo(screenshot)

    # Try multiple selectors for the email field
    email_selectors = [
        '#email',
        'input[name="email"]',
        'input[type="email"]',
        'input[placeholder="Email address or phone number"]',
        'input[placeholder="Email or phone number"]',
    ]

    email_found = False
    for selector in email_selectors:
        try:
            page.wait_for_selector(selector, timeout=5000)
            page.fill(selector, FB_EMAIL)
            print(f"  ✅ Email filled via: {selector}")
            email_found = True
            break
        except Exception:
            continue

    if not email_found:
        # Try the dedicated login page as fallback
        print("  ↩️  Email field not found, trying /login page...")
        page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        accept_cookies(page)

        # Screenshot the login page too
        screenshot2 = page.screenshot()
        send_telegram_photo(screenshot2)

        for selector in email_selectors:
            try:
                page.wait_for_selector(selector, timeout=5000)
                page.fill(selector, FB_EMAIL)
                print(f"  ✅ Email filled via: {selector}")
                email_found = True
                break
            except Exception:
                continue

    if not email_found:
        raise Exception("Could not find email input field on Facebook — check screenshot sent to Telegram")

    time.sleep(1)

    # Fill password
    password_selectors = ['#pass', 'input[name="pass"]', 'input[type="password"]']
    for selector in password_selectors:
        try:
            page.fill(selector, FB_PASSWORD)
            print(f"  ✅ Password filled")
            break
        except Exception:
            continue

    time.sleep(1)
    page.click('[name="login"]')

    try:
        page.wait_for_url(lambda url: "login" not in url, timeout=20000)
    except PlaywrightTimeoutError:
        screenshot3 = page.screenshot()
        send_telegram_photo(screenshot3)
        raise Exception("Facebook login failed — check the screenshot sent to Telegram")

    time.sleep(3)
    print("  ✅ Logged in successfully")


def dismiss_modal(page):
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


def scrape_listings(page):
    print(f"🔍 Searching: {SEARCH_QUERY} in {CITY.title()}...")
    page.goto(MARKETPLACE_URL, wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)

    dismiss_modal(page)

    for _ in range(4):
        page.evaluate("window.scrollBy(0, 900)")
        time.sleep(1.5)

    listings = page.evaluate(r"""
        () => {
            const results = [];
            const links = document.querySelectorAll('a[href*="/marketplace/item/"]');
            const seenHrefs = new Set();

            links.forEach(link => {
                const href = link.href.split('?')[0];
                if (seenHrefs.has(href)) return;
                seenHrefs.add(href);

                const match = href.match(/item\/([0-9]+)/);
                if (!match) return;
                const id = match[1];

                let container = link;
                for (let i = 0; i < 5; i++) {
                    if (container.parentElement) container = container.parentElement;
                    if (container.innerText && container.innerText.length > 10) break;
                }

                const allText = container ? container.innerText : '';
                const lines = allText.split('\n').map(s => s.trim()).filter(Boolean);
                const priceLine = lines.find(l => l.match(/^\$[\d,]+/)) || 'Price not listed';
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
        print("❌ FB_EMAIL or FB_PASSWORD missing!")
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
                print("  ⚠️  No listings found")

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
