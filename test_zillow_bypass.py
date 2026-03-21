import os
import sys
import time

# Set up Django environment so we can import your Project's modules
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'zillow_scraper.settings')
try:
    import django
    django.setup()
except Exception as e:
    print(f"Could not setup django: {e}")
    sys.exit(1)

from scrapers.base import BaseScraper

def run_test():
    print("====================================")
    print("🚀 Zillow Scraper Bypass Test Script")
    print("====================================")
    
    scraper = BaseScraper()
    url = "https://www.zillow.com/profile/Pardee-Properties/"
    
    print(f"Testing URL: {url}")
    print("Making request using curl_cffi with Chrome impersonation...")
    
    start_time = time.time()
    try:
        # Calling get() from your BaseScraper which now uses curl_cffi
        response = scraper.get(url, use_proxy=True)
        elapsed = time.time() - start_time
        
        print("\n--- Results ---")
        print(f"Status Code: {response.status_code} OK")
        print(f"Latency: {elapsed * 1000:.2f} ms")
        print(f"Page Size: {len(response.content)} bytes")
        
        if response.status_code == 200:
            print("✅ SUCCESS! Zillow's 403 block was bypassed on the first try.")
        else:
            print(f"⚠️ Warning: Received HTTP {response.status_code}")
            
    except Exception as e:
        elapsed = time.time() - start_time
        print("\n--- Results ---")
        print(f"Latency: {elapsed * 1000:.2f} ms")
        print(f"❌ ERROR: Request failed: {e}")

if __name__ == "__main__":
    run_test()
