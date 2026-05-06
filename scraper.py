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

# 👇 ADD OR REMOVE SEARCH TERMS HERE — one per line
SEARCH_QUERIES = [
    "Ryobi 36V",
]

# 👇 CHANGE CITY HERE (must be a major city — suburbs not supported by Facebook)
CITY = "sydney"

SEEN_FILE = "seen.json"
# ─────────────────────────────────────────────────────────────────────────────


def marketplace_url(query):
    return (
        f"https://www.facebook.com/marketplace/{CITY}/search"
        f"?query={query.replace(' ', '%20')}&sortBy=creation_time_descend"
    )


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
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        r = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID},
            files={"photo": ("screenshot.png", image_bytes, "image/png")},
            timeout=20
        )
        r.raise_for_status()
        print("  📸 Screenshot sent to Telegram")
    except Exception as e:
        print(f"  ❌ Screenshot send error: {e}")


def accept_cookies(page):
    for selector in [
        'button[data-cookiebanner="accept_button"]',
        '[aria-label="Allow all cookies"]',
        'button:has-text("Accept All")',
        'button:has-text("Allow all cookies")',
        'button:has-text("OK")',
    ]:
        try:
            page.click(selector, timeout=3000)
            print("  ✅ Cookie banner dismissed")
            time.sleep(1)
            return
        except Exception:
            pass


def do_login(page):
    """Log into Facebook and wait until fully redirected away from login."""
    print("🔐 Logging into Facebook...")
    page.goto("https://www.facebook.com/login", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    accept_cookies(page)
    time.sleep(1)

    # Fill email
    email_found = False
    for selector in ['input[type="email"]', 'input[name="email"]', '#email']:
        try:
            page.wait_for_selector(selector, state="visible", timeout=8000)
            page.click(selector)
            time.sleep(0.5)
            page.fill(selector, FB_EMAIL)
            print("  ✅ Email filled")
            email_found = True
            break
        except Exception:
            continue

    if not email_found:
        send_telegram_photo(page.screenshot())
        raise Exception("Could not find email field")

    time.sleep(1)

    # Fill password
    for selector in ['input[type="password"]', 'input[name="pass"]', '#pass']:
        try:
            page.wait_for_selector(selector, state="visible", timeout=8000)
            page.click(selector)
            time.sleep(0.5)
            page.fill(selector, FB_PASSWORD)
            print("  ✅ Password filled")
            break
        except Exception:
            continue

    time.sleep(1)
    print("  🖱️  Clicking Log in...")
    time.sleep(1)

    # Click via JavaScript — most reliable
    page.evaluate("""
        () => {
            const submitBtn = document.querySelector("button[type=submit]");
            if (submitBtn) { submitBtn.click(); return; }
            const inputSubmit = document.querySelector("input[type=submit]");
            if (inputSubmit) { inputSubmit.click(); return; }
            const allButtons = Array.from(document.querySelectorAll("button"));
            const loginBtn = allButtons.find(b => b.innerText.trim().toLowerCase().includes("log in"));
            if (loginBtn) loginBtn.click();
        }
    """)

    # Wait to be redirected away from login page
    try:
        page.wait_for_url(lambda url: "login" not in url, timeout=25000)
    except PlaywrightTimeoutError:
        send_telegram_photo(page.screenshot())
        raise Exception("Still on login page after submit — screenshot sent to Telegram")

    time.sleep(4)
    print("  ✅ Logged in successfully!")


def dismiss_modal(page):
    for selector in ['[aria-label="Close"]', '[role="dialog"] [aria-label="Close"]']:
        try:
            page.click(selector, timeout=2000)
            time.sleep(1)
            break
        except Exception:
            pass


def scrape_listings(page, query):
    url = marketplace_url(query)
    print(f"🔍 Searching: '{query}' in {CITY.title()}...")

    # Navigate to marketplace search — use networkidle so JS fully renders
    page.goto(url, wait_until="networkidle", timeout=45000)
    time.sleep(5)

    # If Facebook redirected us to login, log in and come back
    if "login" in page.url or "login" in page.content()[:2000]:
        print("  ⚠️  Got redirected to login — logging in again...")
        do_login(page)
        page.goto(url, wait_until="networkidle", timeout=45000)
        time.sleep(5)

    dismiss_modal(page)

    # Wait for listing cards to appear
    print("  ⏳ Waiting for listings to render...")
    try:
        page.wait_for_selector('a[href*="/marketplace/item/"]', timeout=20000, state="attached")
        print("  ✅ Listings detected")
    except PlaywrightTimeoutError:
        print("  🔄 No listings yet — reloading...")
        page.reload(wait_until="networkidle", timeout=45000)
        time.sleep(5)
        try:
            page.wait_for_selector('a[href*="/marketplace/item/"]', timeout=20000, state="attached")
            print("  ✅ Listings detected after reload")
        except PlaywrightTimeoutError:
            print("  ⚠️  Still no listings — sending screenshot...")
            send_telegram_photo(page.screenshot())
            return []

    # Scroll to trigger lazy-loaded cards
    for _ in range(5):
        page.evaluate("window.scrollBy(0, 800)")
        time.sleep(1.5)
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)

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
                for (let i = 0; i < 6; i++) {
                    if (container.parentElement) container = container.parentElement;
                    if (container.innerText && container.innerText.trim().length > 15) break;
                }

                const allText = container ? container.innerText : '';
                const lines = allText.split('\n').map(s => s.trim()).filter(Boolean);
                const priceLine = lines.find(l => l.match(/^\$[\d,]+/)) || 'Price not listed';
                const titleLine = lines
                    .filter(l => l.length > 5 && !l.match(/^\$/) && !l.match(/^\d+ (min|hr|day|s ago)/i))
                    .sort((a, b) => b.length - a.length)[0] || 'Marketplace item';

                results.push({ id, title: titleLine, price: priceLine, url: href });
            });

            return results;
        }
    """)

    print(f"  Found {len(listings)} listings")
    return listings


def run():
    print("=" * 50)
    print(f"🤖 Marketplace Bot — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔎 Searches: {', '.join(SEARCH_QUERIES)}")
    print("=" * 50)

    if not FB_EMAIL or not FB_PASSWORD:
        print("❌ FB_EMAIL or FB_PASSWORD missing!")
        send_telegram("⚠️ Bot error: Facebook credentials not set in Railway variables.")
        return

    seen = load_seen()
    new_count = 0

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            "./session",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-AU",
            ignore_https_errors=True,
        )
        page = context.new_page()

        try:
            # Always log in fresh each run — Railway resets filesystem so session won't persist anyway
            print("🔐 Logging in fresh this run...")
            do_login(page)

            for query in SEARCH_QUERIES:
                try:
                    listings = scrape_listings(page, query)
                except Exception as e:
                    print(f"  ❌ Error scraping '{query}': {e}")
                    send_telegram(f"⚠️ Error scraping '{query}': {e}")
                    continue

                for item in listings:
                    if item["id"] in seen:
                        print(f"  ⏭  Already seen: {item['id']}")
                        continue

                    print(f"  🆕 New: {item['title']} — {item['price']}")
                    msg = (
                        f"🔔 *New Ryobi 36V listing!*\n\n"
                        f"*{item['title']}*\n"
                        f"💰 {item['price']}\n"
                        f"📍 {CITY.title()}, NSW\n"
                        f"🔗 {item['url']}"
                    )
                    send_telegram(msg)
                    seen.add(item["id"])
                    new_count += 1
                    time.sleep(1.5)

                time.sleep(2)

            save_seen(seen)

        except Exception as e:
            print(f"❌ Error during run: {e}")
            send_telegram(f"⚠️ Bot error: {e}")
        finally:
            context.close()

    print(f"\n✅ Done. {new_count} new listing(s) found.")
    print("=" * 50)


if __name__ == "__main__":
    run()
