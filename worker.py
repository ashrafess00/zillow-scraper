import asyncio
import json
import logging
import redis.asyncio as redis
from playwright.async_api import async_playwright

# Use the specific submodule path where the async function actually lives
from playwright_stealth.stealth import stealth_async as stealth

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PROXY_URL = "http://04c2a2c98864ae89:QnjrV4d8T3k61U0S@res.geonix.com:10000"
TARGET_URL = "https://www.zillow.com/profile/Pardee-Properties/"
# Connecting to the Redis instance we saw in your htop output
REDIS_URL = "redis://localhost:6381/0" 

async def fetch_and_store_cookies():
    # Connect to local Redis
    r = redis.from_url(REDIS_URL, decode_responses=True)
    
    async with async_playwright() as p:
        logging.info("Launching headless browser...")
        
        # Launch Chromium with anti-detection flags
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        
        # Create a new context with your proxy and a realistic user agent
        context = await browser.new_context(
            proxy={"server": PROXY_URL},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        
        page = await context.new_page()
        
        # Apply the stealth plugin to hide Playwright's default fingerprints
        await stealth(page)
        
        logging.info(f"Navigating to {TARGET_URL}...")
        try:
            # Load the page and wait for the DOM to settle
            await page.goto(TARGET_URL, timeout=45000, wait_until="domcontentloaded")
            
            # Wait 5 seconds to allow Datadome/PerimeterX JavaScript to execute and set cookies
            logging.info("Waiting 5 seconds for JS challenges to resolve...")
            await page.wait_for_timeout(5000) 
            
            # Check the page source to see if we got blocked
            page_content = await page.content()
            if "captcha" in page_content.lower() or "perimeterx" in page_content.lower():
                logging.warning("Blocked: CAPTCHA detected on page. The proxy IP may be flagged.")
            else:
                logging.info("Success: Page loaded without CAPTCHA. Extracting cookies...")
                
            # Harvest all cookies from the context
            cookies = await context.cookies()
            
            if cookies:
                cookie_json = json.dumps(cookies)
                # Store in Redis with a 15-minute expiration (900 seconds)
                await r.set("zillow_auth_cookies", cookie_json, ex=900)
                logging.info(f"Saved {len(cookies)} cookies to Redis under key 'zillow_auth_cookies'.")
            else:
                logging.error("No cookies were found.")

        except Exception as e:
            logging.error(f"Error during navigation: {e}")
        finally:
            await browser.close()
            await r.aclose()

if __name__ == "__main__":
    asyncio.run(fetch_and_store_cookies())