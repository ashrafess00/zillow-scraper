"""
Debug script to dump raw Zillow JSON card data keys.
Shows what fields are available for for-sale vs for-rent listings.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'zillow_scraper.settings')
import django; django.setup()

from scrapers.property_scraper import property_scraper
from scrapers.utils import build_search_url
from bs4 import BeautifulSoup

def dump_card_keys(location, list_type):
    url = build_search_url(location, list_type)
    print(f"\n{'='*60}")
    print(f"Location: {location}, Type: {list_type}")
    print(f"URL: {url}")
    print(f"{'='*60}")
    
    try:
        response = property_scraper.get(url)
        soup = BeautifulSoup(response.text, 'lxml')
        
        for script in soup.find_all('script'):
            text = script.string or ''
            if len(text) < 1000:
                continue
            if '"listResults"' not in text and '"searchResults"' not in text:
                continue
            
            try:
                data = json.loads(text)
            except:
                continue
            
            # Find listResults
            search_paths = [
                lambda d: d.get('props', {}).get('pageProps', {}).get('searchPageState', {}).get('cat1', {}).get('searchResults', {}),
                lambda d: d.get('props', {}).get('pageProps', {}).get('searchResults', {}),
                lambda d: d.get('searchResults', {}),
                lambda d: d.get('cat1', {}).get('searchResults', {}),
            ]
            
            for path_func in search_paths:
                try:
                    sr = path_func(data)
                    results = sr.get('listResults', [])
                    if results:
                        # Dump first 2 items
                        for i, item in enumerate(results[:2]):
                            print(f"\n--- Item {i} ---")
                            print(f"Top-level keys: {list(item.keys())}")
                            
                            # Show specific nested structures
                            if 'latLong' in item:
                                print(f"  latLong: {item['latLong']}")
                            if 'hdpData' in item:
                                hdp = item['hdpData']
                                print(f"  hdpData keys: {list(hdp.keys()) if isinstance(hdp, dict) else type(hdp)}")
                                if isinstance(hdp, dict) and 'homeInfo' in hdp:
                                    hi = hdp['homeInfo']
                                    print(f"  hdpData.homeInfo keys: {list(hi.keys()) if isinstance(hi, dict) else type(hi)}")
                                    # Show lat/lng specific fields
                                    for k in ['latitude', 'longitude', 'zipcode', 'price', 'bedrooms', 'bathrooms', 'livingArea']:
                                        if k in hi:
                                            print(f"    homeInfo.{k}: {hi[k]}")
                            if 'units' in item:
                                units = item['units']
                                print(f"  units: {units[:2] if isinstance(units, list) else units}")
                            
                            # Show price-related fields
                            for k in ['price', 'unformattedPrice', 'beds', 'baths', 'area', 'livingArea',
                                      'latitude', 'longitude', 'zpid', 'statusType', 'statusText',
                                      'minPrice', 'maxPrice', 'minBeds', 'minBaths']:
                                if k in item:
                                    print(f"  {k}: {item[k]}")
                        
                        print(f"\nTotal items in listResults: {len(results)}")
                        return
                except:
                    continue
        
        print("No listResults found in any script tag")
    except Exception as e:
        print(f"Error: {e}")

# Test both types
dump_card_keys('seattle-wa', 'for-sale')
dump_card_keys('seattle-wa', 'for-rent')
