"""
Property scraper for Zillow property listings.
"""

import re
import json
import math
import logging
from typing import Optional, Dict, List, Any
from urllib.parse import urlencode, quote

from bs4 import BeautifulSoup

from django.core.cache import cache

from .base import BaseScraper, NotFoundException, ScraperException, BlockedException
from .utils import (
    extract_json_from_script,
    extract_apollo_state,
    parse_property_card,
    clean_price,
    clean_number,
    clean_text,
    build_search_url,
    extract_broad_location,
    slugify_location,
)

logger = logging.getLogger(__name__)

# The raw property object parsed out of a homedetails page is cached in Redis so
# that all detail endpoints (/property, /zestimate, /priceHistory, ...) for the
# same zpid share a single Zillow fetch. Matches the 15-min response cache in
# api/urls.py — details like price/status can move, so we don't cache longer.
PROPERTY_CACHE_TIMEOUT = 60 * 15

# HOA fees arrive two ways: `monthlyHoaFee` (already monthly, preferred) or a
# resoFacts string like "$529 semi-annually" that must be normalized to monthly.
HOA_PERIOD_DIVISORS = {
    'annually': 12.0, 'annual': 12.0, 'yearly': 12.0, 'year': 12.0,
    'semi-annually': 6.0, 'semiannually': 6.0, 'semi annually': 6.0,
    'quarterly': 3.0, 'quarter': 3.0,
    'bi-monthly': 2.0, 'bimonthly': 2.0,
    'monthly': 1.0, 'month': 1.0,
}

# Zillow does not ship a homeowners-insurance figure on the property object, so
# /monthlyCost estimates it at this share of price per year (Zillow's own
# calculator default). Flagged as an estimate in the response.
INSURANCE_RATE_ANNUAL = 0.0035

# Annual PMI as a share of the loan, applied only when down payment < 20%.
PMI_RATE_ANNUAL = 0.005

# --- /similarHomes -----------------------------------------------------------
# Zillow no longer ships a `nearbyHomes` array on the homedetails property object
# (verified live 2026-07-30), and the comps behind the on-page carousel come from
# `zg-graph`, which answers any query not on its persisted-query safelist with
# QUERY_NOT_IN_SAFELIST. So comps are rebuilt from the search index instead: look
# around the subject, then rank. See get_similar_homes.
EARTH_RADIUS_MILES = 3958.7613

# Half-height of the search box, in degrees of latitude. The tight box is tried
# first; if it yields fewer comps than asked for, the wide box is tried once.
SIMILAR_TIGHT_DELTA_DEG = 0.015   # ~1.0 mi
SIMILAR_WIDE_DELTA_DEG = 0.05     # ~3.5 mi

SIMILAR_HOMES_CACHE_TIMEOUT = 60 * 15

# Ranking weights — lower total score is a closer comp. Proximity and size
# dominate; price is weighted lightly on purpose so that an over- or under-priced
# neighbour still surfaces (that spread is the point of pulling comps at all).
SIMILARITY_WEIGHTS = {
    'distance': 1.0,    # per mile
    'sqft': 2.0,        # per 1.0 of fractional size difference
    'beds': 0.35,       # per bedroom
    'baths': 0.25,      # per bathroom
    'price': 0.5,       # per 1.0 of fractional price difference
    'home_type': 0.75,  # flat penalty when the property type differs
}

# --- /marketStats ------------------------------------------------------------
# Region market data comes from Zillow's home-values page, which carries an
# `odpMarketAnalytics` block inside __NEXT_DATA__. It is a plain page fetch — no
# GraphQL, so it is not subject to the zg-graph persisted-query safelist.
MARKET_STATS_CACHE_TIMEOUT = 60 * 60 * 6  # region aggregates move monthly, not hourly

# Zillow publishes these monthly, so the "latest" figures for listings and sales
# sit on different period ends. Both are reported rather than reconciled.
MARKET_HISTORY_LIMIT = 120

# Zillow's `parentage` array is misnamed: for Austin it holds the five real
# ancestors (country, state, DMA, CBSA, county) *and* all 73 zipcodes contained
# by the city. Ranking the geography lets the descendants be dropped — an entry
# is an ancestor only if it sits strictly above the region's own level. Unranked
# types are kept rather than guessed at.
REGION_HIERARCHY = {
    'country': 0,
    'state': 1,
    'dma': 2,
    'cbsa': 3, 'msa': 3, 'metro': 3,
    'county': 4,
    'city': 5, 'borough': 5,
    'zipcode': 6, 'zip': 6,
    'neighborhood': 7,
}

# Friendly sort names → Zillow searchQueryState.sortSelection tokens.
# Unknown values are passed through unchanged (Zillow ignores tokens it doesn't
# recognize, so this fails soft). Tokens beyond "days"/"globalrelevanceex" are
# best-effort and worth confirming against a live response.
SORT_MAP = {
    'relevant': 'globalrelevanceex',
    'default': 'globalrelevanceex',
    'newest': 'days',
    'price_low': 'pricea',
    'price_high': 'priced',
    'sqft': 'size',
    'lot': 'lot',
    'beds': 'beds',
    'baths': 'baths',
}


def resolve_sort(sort) -> Optional[str]:
    """Map a friendly sort name to a Zillow token; pass unknown tokens through."""
    if not sort:
        return None
    key = str(sort).strip().lower()
    return SORT_MAP.get(key, key)


class PropertyScraper(BaseScraper):
    """Scraper for Zillow property listings."""

    # Zillow's public suggestions service. It lives on the zillowstatic CDN host,
    # not www.zillow.com — the same path under www 404s.
    AUTOCOMPLETE_URL = "https://www.zillowstatic.com/autocomplete/v3/suggestions"

    @staticmethod
    def _apply_list_type(filter_state: Dict, list_type: str) -> None:
        """
        Toggle the filterState flags that select for-sale / for-rent / sold.

        Zillow defaults to for-sale, so that case is left untouched. For rent and
        sold we flip the relevant flag on and the competing ones off.
        """
        lt = (list_type or 'for-sale').lower()
        SALE_FLAGS = ('isForSaleByAgent', 'isForSaleByOwner', 'isNewConstruction',
                      'isComingSoon', 'isAuction', 'isForSaleForeclosure')
        if lt in ('for-rent', 'rent'):
            filter_state['isForRent'] = {'value': True}
            filter_state['isRecentlySold'] = {'value': False}
            for flag in SALE_FLAGS:
                filter_state[flag] = {'value': False}
        elif lt in ('sold', 'recently-sold'):
            filter_state['isRecentlySold'] = {'value': True}
            filter_state['isForRent'] = {'value': False}
            for flag in SALE_FLAGS:
                filter_state[flag] = {'value': False}

    def _build_search_query_state(self, list_type: str = 'for-sale', sort=None, **filters) -> Dict:
        """Build Zillow search query state object."""
        filter_state = {}
        
        # Price filters
        if filters.get('minPrice'):
            filter_state['price'] = filter_state.get('price', {})
            filter_state['price']['min'] = filters['minPrice']
        if filters.get('maxPrice'):
            filter_state['price'] = filter_state.get('price', {})
            filter_state['price']['max'] = filters['maxPrice']
        
        # Beds/Baths
        if filters.get('beds'):
            filter_state['beds'] = {'min': filters['beds']}
        if filters.get('baths'):
            filter_state['baths'] = {'min': filters['baths']}
        
        # Square footage
        if filters.get('minSqft'):
            filter_state['sqft'] = filter_state.get('sqft', {})
            filter_state['sqft']['min'] = filters['minSqft']
        if filters.get('maxSqft'):
            filter_state['sqft'] = filter_state.get('sqft', {})
            filter_state['sqft']['max'] = filters['maxSqft']
        
        # Year built
        if filters.get('minBuilt'):
            filter_state['built'] = filter_state.get('built', {})
            filter_state['built']['min'] = filters['minBuilt']
        if filters.get('maxBuilt'):
            filter_state['built'] = filter_state.get('built', {})
            filter_state['built']['max'] = filters['maxBuilt']
        
        # Lot size
        if filters.get('minLot'):
            filter_state['lotSize'] = filter_state.get('lotSize', {})
            filter_state['lotSize']['min'] = filters['minLot']
        if filters.get('maxLot'):
            filter_state['lotSize'] = filter_state.get('lotSize', {})
            filter_state['lotSize']['max'] = filters['maxLot']
        
        # HOA
        if filters.get('maxHOA'):
            filter_state['hoa'] = {'max': filters['maxHOA']}
        
        # Property types
        if filters.get('isSingleFamily'):
            filter_state['isSingleFamily'] = {'value': True}
        if filters.get('isCondo'):
            filter_state['isCondo'] = {'value': True}
        if filters.get('isTownhouse'):
            filter_state['isTownhouse'] = {'value': True}
        if filters.get('isApartment'):
            filter_state['isApartment'] = {'value': True}
        if filters.get('isMultiFamily'):
            filter_state['isMultiFamily'] = {'value': True}
        if filters.get('isLotLand'):
            filter_state['isLotLand'] = {'value': True}
        if filters.get('isManufactured'):
            filter_state['isManufactured'] = {'value': True}
        
        # Features
        if filters.get('hasPool'):
            filter_state['hasPool'] = {'value': True}
        if filters.get('hasGarage'):
            filter_state['hasGarage'] = {'value': True}
        if filters.get('parkingSpots'):
            filter_state['parkingSpots'] = {'min': filters['parkingSpots']}
        if filters.get('singleStory'):
            filter_state['singleStory'] = {'value': True}
        
        # Views
        if filters.get('isWaterView'):
            filter_state['isWaterfront'] = {'value': True}
        if filters.get('isMountainView'):
            filter_state['isMountainView'] = {'value': True}
        if filters.get('isParkView'):
            filter_state['isParkView'] = {'value': True}
        if filters.get('isCityView'):
            filter_state['isCityView'] = {'value': True}
        
        # Basement
        if filters.get('isBasementFinished'):
            filter_state['isBasementFinished'] = {'value': True}
        if filters.get('isBasementUnfinished'):
            filter_state['isBasementUnfinished'] = {'value': True}
        
        # Status
        if filters.get('isComingSoon'):
            filter_state['isComingSoon'] = {'value': True}
        if filters.get('isForSaleForeclosure'):
            filter_state['isForSaleForeclosure'] = {'value': True}
        if filters.get('isAuction'):
            filter_state['isAuction'] = {'value': True}
        if filters.get('isOpenHousesOnly'):
            filter_state['isOpenHouse'] = {'value': True}
        if filters.get('is3dHome'):
            filter_state['is3dHome'] = {'value': True}
        
        # Days on Zillow
        if filters.get('daysOnZillow'):
            filter_state['daysOnZillow'] = {'value': filters['daysOnZillow']}

        # Listing type (for-sale / for-rent / sold)
        self._apply_list_type(filter_state, list_type)

        # Sort order
        sort_token = resolve_sort(sort)
        if sort_token:
            filter_state['sortSelection'] = {'value': sort_token}

        return filter_state
    
    def _parse_search_results(self, soup) -> Dict[str, Any]:
        """Parse property search results from page.
        
        Returns:
            Dict with 'results' (list of properties) and 'total_results' (int)
        """
        properties = []
        total_results = 0
        
        # Helper to find total count recursively
        def find_total(obj):
            if isinstance(obj, dict):
                # Check common keys
                for key in ['totalResultCount', 'resultCount', 'totalCount']:
                    if key in obj and isinstance(obj[key], (int, str)):
                        try:
                            val = int(obj[key])
                            if val > 0:  # Accept any positive count
                                return val
                        except:
                            pass
                
                # Check if this object IS the search results container
                if 'listResults' in obj:
                    for key in ['totalResultCount', 'resultCount', 'totalCount']:
                        if key in obj:
                            try:
                                return int(obj[key])
                            except:
                                pass

                # Recurse
                for v in obj.values():
                    res = find_total(v)
                    if res: return res
            elif isinstance(obj, list):
                for item in obj:
                    res = find_total(item)
                    if res: return res
            return 0

        # Try to find JSON data in script tags
        for script in soup.find_all('script'):
            script_text = script.string or ''
            
            # Skip short scripts
            if len(script_text) < 1000:
                continue
            
            # Try to parse as JSON
            if script_text.strip().startswith('{') or '"searchResults"' in script_text or '"listResults"' in script_text:
                try:
                    data = json.loads(script_text)
                    
                    # 1. Try finding total count recursively anywhere in the JSON
                    found_total = find_total(data)
                    if found_total > 0:
                        total_results = found_total
                    
                    # 2. Parse property list (keep existing robust paths)
                    search_results_paths = [
                        lambda d: d.get('props', {}).get('pageProps', {}).get('searchPageState', {}).get('cat1', {}).get('searchResults', {}),
                        lambda d: d.get('props', {}).get('pageProps', {}).get('searchResults', {}),
                        lambda d: d.get('searchResults', {}),
                        lambda d: d.get('cat1', {}).get('searchResults', {}),
                        lambda d: d.get('searchPageState', {}).get('cat1', {}).get('searchResults', {}),
                    ]
                    
                    for path_func in search_results_paths:
                        try:
                            search_results = path_func(data)
                            if search_results and isinstance(search_results, dict):
                                results = search_results.get('listResults', [])
                                if results and isinstance(results, list):
                                    
                                    # Extract current page
                                    current_page = (
                                        search_results.get('pagination', {}).get('currentPage') or
                                        search_results.get('currentPage') or
                                        1
                                    )
                                    
                                    for result in results:
                                        parsed = parse_property_card(result)
                                        if parsed and (parsed.get('address') or parsed.get('zpid')):
                                            properties.append(parsed)
                                    if properties:
                                        # Use found total, or count of properties if still 0
                                        if total_results == 0:
                                            total_results = len(properties)
                                            
                                        logger.info(f"Found {len(properties)} properties from JSON (total: {total_results}, page: {current_page})")
                                        return {
                                            'results': properties,
                                            'total_results': total_results,
                                            'current_page': current_page
                                        }
                        except (KeyError, TypeError, AttributeError):
                            continue
                            
                except json.JSONDecodeError:
                    continue
        
        # Also try Apollo state
        if not properties:
            apollo_state = extract_apollo_state(soup)
            if apollo_state:
                for key, value in apollo_state.items():
                    if isinstance(value, dict) and value.get('zpid'):
                        parsed = parse_property_card(value)
                        if parsed:
                            properties.append(parsed)
        
        # Fallback: Parse HTML
        if not properties:
            logger.info("No properties found in scripts, trying HTML parsing...")
            # Try multiple selectors
            selectors = [
                '[data-test="property-card"]',
                '.list-card',
                '.property-card',
                'article[data-test]',
                '[class*="StyledPropertyCard"]',
                'li[class*="ListItem"]',
                'a[href*="/homedetails/"]',
            ]
            
            for selector in selectors:
                cards = soup.select(selector)
                if cards:
                    logger.info(f"Found {len(cards)} elements with selector: {selector}")
                    break
            else:
                cards = []
            
            for card in cards:
                address_elem = card.select_one('[data-test="property-card-addr"], .list-card-addr, address, [class*="address"]')
                price_elem = card.select_one('[data-test="property-card-price"], .list-card-price, [class*="price"]')
                link_elem = card.select_one('a[href*="/homedetails/"], a[href*="zpid"]')
                details_elem = card.select_one('[data-test="property-card-details"], .list-card-details, [class*="details"]')
                
                if address_elem or link_elem:
                    prop = {
                        'zpid': None,
                        'address': clean_text(address_elem.get_text()) if address_elem else '',
                        'url': '',
                        'price': clean_price(price_elem.get_text()) if price_elem else None,
                        'beds': None,
                        'baths': None,
                        'sqft': None,
                    }
                    
                    # Handle if card itself is a link
                    if card.name == 'a' and '/homedetails/' in card.get('href', ''):
                        link_elem = card
                    
                    if link_elem:
                        href = link_elem.get('href', '')
                        prop['url'] = f"{self.BASE_URL}{href}" if href.startswith('/') else href
                        # Extract zpid
                        zpid_match = re.search(r'(\d+)_zpid', href)
                        if zpid_match:
                            prop['zpid'] = int(zpid_match.group(1))
                    
                    # Parse beds/baths/sqft from details
                    if details_elem:
                        details_text = details_elem.get_text()
                        beds_match = re.search(r'(\d+)\s*b[de]', details_text, re.I)
                        baths_match = re.search(r'(\d+)\s*ba', details_text, re.I)
                        sqft_match = re.search(r'([\d,]+)\s*sq', details_text, re.I)
                        
                        if beds_match:
                            prop['beds'] = int(beds_match.group(1))
                        if baths_match:
                            prop['baths'] = int(baths_match.group(1))
                        if sqft_match:
                            prop['sqft'] = int(sqft_match.group(1).replace(',', ''))
                    
                    if prop.get('address') or prop.get('zpid'):
                        properties.append(prop)
        
        # For fallback paths, we don't have total_results from JSON
        # Return count of found properties as total (best effort)
        return {'results': properties, 'total_results': len(properties)}
    
    def search_by_location(
        self,
        location: str,
        list_type: str = 'for-sale',
        page: int = 1,
        sort=None,
        **filters
    ) -> Dict[str, Any]:
        """
        Search properties by location.
        
        Handles:
        - Location slugs ("seattle-wa")
        - Full addresses ("35 Morse Ave Bloomfield, NJ 07003") 
          → may redirect to /homedetails/ for exact match
        - Broad locations ("Bloomfield NJ")
        
        If an exact address returns 404, falls back to a broader city+state search.
        
        Args:
        	location: Location string
        	list_type: 'for-sale', 'for-rent', or 'sold'
        	page: Page number
        	**filters: Additional search filters
        	
        Returns:
        	Dict with 'results', 'total_results', and 'current_page'
        """
        if page > 20:
            raise NotFoundException("Zillow search results are limited to 20 pages (800 properties).")
            
        sort_token = resolve_sort(sort)
        url = build_search_url(location, list_type, page, sort=sort_token)

        try:
            return self._fetch_and_parse_location(url, location, list_type, page)
        except NotFoundException:
            # If the exact address 404'd, try a broader location (city + state)
            broad = extract_broad_location(location)
            broad_slug = slugify_location(location)
            if broad != broad_slug:
                logger.info(f"Exact address not found, trying broader location: {broad}")
                broad_url = build_search_url(broad, list_type, page, sort=sort_token)
                try:
                    return self._fetch_and_parse_location(broad_url, broad, list_type, page)
                except NotFoundException:
                    raise NotFoundException(f"No properties found for location: {location}")
            raise
        except Exception as e:
            logger.error(f"Failed to search by location: {e}")
            raise ScraperException(f"Failed to search properties: {e}")
    
    def _fetch_and_parse_location(
        self,
        url: str,
        location: str,
        list_type: str,
        page: int
    ) -> Dict[str, Any]:
        """
        Fetch a Zillow URL and parse results.
        Handles redirects to /homedetails/ for exact address matches.
        """
        # Use self.get() instead of get_soup() to access response.url (final URL after redirects)
        response = self.get(url)
        final_url = response.url
        soup = BeautifulSoup(response.text, 'lxml')
        
        # If Zillow redirected to a property detail page, parse it as a single property
        if '/homedetails/' in final_url:
            logger.info(f"Redirected to property detail: {final_url}")
            property_data = self._parse_property_details(soup, final_url)
            if property_data:
                return {
                    'results': [property_data],
                    'total_results': 1,
                    'current_page': 1
                }
            raise NotFoundException(f"No property details found at: {final_url}")
        
        # Normal search results page
        parsed = self._parse_search_results(soup)
        
        if not parsed.get('results'):
            raise NotFoundException(f"No properties found for location: {location}")
        
        parsed['current_page'] = page
        return parsed
    
    def search_by_coordinates(
        self,
        lat: float,
        lng: float,
        list_type: str = 'for-sale',
        page: int = 1,
        sort=None,
        **filters
    ) -> Dict[str, Any]:
        """
        Search properties by coordinates.
        
        Args:
            lat: Latitude
            lng: Longitude
            list_type: 'for-sale', 'for-rent', or 'sold'
            page: Page number
            **filters: Additional search filters
            
        Returns:
            Dict with 'results', 'total_results', and 'current_page'
        """
        # Create a small bounding box around coordinates
        delta = 0.05  # Approximately 3.5 miles
        
        return self.search_by_map_bounds(
            north=lat + delta,
            south=lat - delta,
            east=lng + delta,
            west=lng - delta,
            list_type=list_type,
            page=page,
            sort=sort,
            **filters
        )
    
    def search_by_map_bounds(
        self,
        north: float,
        south: float,
        east: float,
        west: float,
        list_type: str = 'for-sale',
        page: int = 1,
        sort=None,
        **filters
    ) -> Dict[str, Any]:
        """
        Search properties by map bounds.
        
        Args:
            north: Northern latitude
            south: Southern latitude
            east: Eastern longitude
            west: Western longitude
            list_type: 'for-sale', 'for-rent', or 'sold'
            page: Page number
            **filters: Additional search filters
            
        Returns:
            Dict with 'results', 'total_results', and 'current_page'
        """
        if page > 20:
            raise NotFoundException("Zillow search results are limited to 20 pages (800 properties).")
            
        # Build search query state
        map_bounds = {
            'north': north,
            'south': south,
            'east': east,
            'west': west,
        }
        
        filter_state = self._build_search_query_state(
            list_type=list_type, sort=sort, **filters
        )

        search_query_state = {
            'mapBounds': map_bounds,
            'isMapVisible': True,
            'filterState': filter_state,
            'isListVisible': True,
        }
        
        if page > 1:
            search_query_state['pagination'] = {'currentPage': page}
        
        # URL encode the query state
        query_string = urlencode({
            'searchQueryState': json.dumps(search_query_state)
        })
        
        url = f"{self.BASE_URL}/homes/?{query_string}"
        
        try:
            soup = self.get_soup(url)
            parsed = self._parse_search_results(soup)
            
            if not parsed.get('results'):
                raise NotFoundException("No properties found in specified bounds")
            
            # Add current page
            parsed['current_page'] = page
            return parsed
            
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to search by map bounds: {e}")
            raise ScraperException(f"Failed to search properties: {e}")
    
    def search_by_mls_id(self, mls_id: str, page: int = 1, **filters) -> Dict[str, Any]:
        """
        Search properties by MLS ID.
        
        Args:
            mls_id: MLS listing ID
            page: Page number
            **filters: Additional search filters
            
        Returns:
            Dict with 'results' (list), 'total_results', and 'current_page'
        """
        if page > 20:
            raise NotFoundException("Zillow search results are limited to 20 pages (800 properties).")
            
        try:
            # Search for the MLS ID
            search_url = f"{self.BASE_URL}/homes/{mls_id}/"
            
            # Add pagination if needed
            if page > 1:
                if search_url.endswith('/'):
                    search_url = f"{search_url}{page}_p/"
                else:
                    search_url = f"{search_url}/{page}_p/"
            
            soup = self.get_soup(search_url)
            properties = self._parse_search_results(soup)
            
            if not properties.get('results'):
                raise NotFoundException(f"No properties found for MLS ID: {mls_id}")
            
            # Ensure current page is set
            if 'current_page' not in properties:
                properties['current_page'] = page
                
            return properties
            
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to search by MLS ID: {e}")
            raise ScraperException(f"Failed to search by MLS ID: {e}")
    
    def search_by_polygon(
        self,
        polygon: str,
        list_type: str = 'for-sale',
        page: int = 1,
        sort=None,
        **filters
    ) -> Dict[str, Any]:
        """
        Search properties by polygon coordinates.
        
        Args:
            polygon: Semicolon-separated coordinates (lat,lng;lat,lng;...)
            list_type: 'for-sale', 'for-rent', or 'sold'
            page: Page number
            **filters: Additional search filters
            
        Returns:
            Dict with 'results', 'total_results', and 'current_page'
        """
        # Parse polygon coordinates
        coords = []
        for point in polygon.split(';'):
            parts = point.strip().split(',')
            if len(parts) == 2:
                coords.append({
                    'lat': float(parts[0]),
                    'lng': float(parts[1])
                })
        
        if len(coords) < 3:
            raise ValueError("Polygon must have at least 3 points")
        
        # Calculate bounding box from polygon
        lats = [c['lat'] for c in coords]
        lngs = [c['lng'] for c in coords]
        
        return self.search_by_map_bounds(
            north=max(lats),
            south=min(lats),
            east=max(lngs),
            west=min(lngs),
            list_type=list_type,
            page=page,
            sort=sort,
            **filters
        )
    
    def search_by_url(self, url: str) -> Dict[str, Any]:
        """
        Parse a Zillow URL and return results.
        Handles both search result pages and individual property detail pages.
        
        Args:
            url: Full Zillow URL (search results or property detail)
            
        Returns:
            Dict with 'results' (list), 'total_results', and 'current_page'
        """
        try:
            soup = self.get_soup(url)
            
            # Check if page is blocked
            title = soup.find('title')
            title_text = title.get_text().lower() if title else ''
            logger.info(f"Page title: '{title_text}', page size: {len(str(soup))}")
            
            if 'denied' in title_text or 'blocked' in title_text or 'captcha' in title_text:
                logger.warning(f"Block detected! Title: {title_text}")
                raise BlockedException("Request blocked by Zillow - access denied")
            
            # Check if this is a single property detail page (/homedetails/)
            if '/homedetails/' in url:
                property_data = self._parse_property_details(soup, url)
                if property_data:
                    return {
                        'results': [property_data],
                        'total_results': 1,
                        'current_page': 1
                    }
                raise NotFoundException("No property details found at URL")
            
            # Otherwise, treat as search results page
            # Note: We don't control the page number here as it comes from the URL
            parsed = self._parse_search_results(soup)
            
            if not parsed.get('results'):
                raise NotFoundException("No properties found at URL")
            
            # Try to extract page number from URL if not available or if it's 1 (default)
            # URL patterns: /2_p/ or directory/2_p/
            if parsed.get('current_page', 1) == 1:
                page_match = re.search(r'/(\d+)_p/', url)
                if page_match:
                    parsed['current_page'] = int(page_match.group(1))
            
            # Ensure proper defaults
            if 'current_page' not in parsed:
                parsed['current_page'] = 1
                
            return parsed
            
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to parse URL: {e}")
            raise ScraperException(f"Failed to parse URL: {e}")
    
    def _parse_property_details(self, soup, url: str) -> Optional[Dict]:
        """Parse a single property detail page."""
        try:
            script_data = extract_json_from_script(soup)
            
            if not script_data:
                return None
            
            property_data = {}
            
            # Try new structure: componentProps.gdpClientCache (JSON string)
            component_props = script_data.get('componentProps', {})
            gdp_cache = component_props.get('gdpClientCache', '')
            
            if isinstance(gdp_cache, str) and gdp_cache:
                try:
                    gdp_data = json.loads(gdp_cache)
                    # Find any key that contains a 'property' object
                    for key, value in gdp_data.items():
                        if isinstance(value, dict) and 'property' in value:
                            property_data = value.get('property', {})
                            if property_data:
                                logger.info(f"Found property data in gdpClientCache")
                                break
                except json.JSONDecodeError:
                    pass
            
            # Fallback to old structure
            if not property_data:
                property_data = (
                    script_data.get('property', {}) or
                    script_data.get('propertyDetails', {}) or
                    script_data.get('listing', {}) or
                    {}
                )
            
            # Extract zpid from URL if not in data
            zpid = property_data.get('zpid')
            if not zpid:
                import re
                match = re.search(r'/(\d+)_zpid', url)
                if match:
                    zpid = int(match.group(1))
            
            # Build address from components
            address_parts = []
            street = property_data.get('streetAddress', '')
            city = property_data.get('city', '')
            state = property_data.get('state', '')
            zipcode = property_data.get('zipcode', '')
            
            if street:
                address_parts.append(street)
            if city:
                address_parts.append(city)
            if state:
                address_parts.append(state)
            if zipcode:
                address_parts.append(zipcode)
            
            address = ', '.join(address_parts) if address_parts else property_data.get('address', '')
            
            # Get photo
            photo_url = ''
            photos = property_data.get('hiResImageLink') or property_data.get('photos', [])
            if isinstance(photos, list) and photos:
                first_photo = photos[0]
                if isinstance(first_photo, dict):
                    photo_url = first_photo.get('mixedSources', {}).get('jpeg', [{}])[0].get('url', '')
                else:
                    photo_url = first_photo
            elif isinstance(photos, str):
                photo_url = photos
                
            return {
                'zpid': zpid,
                'address': address,
                'url': url,
                'photo_url': photo_url,
                'price': clean_price(property_data.get('price') or property_data.get('zestimate')),
                'beds': property_data.get('bedrooms') or property_data.get('beds'),
                'baths': property_data.get('bathrooms') or property_data.get('baths'),
                'sqft': property_data.get('livingArea') or property_data.get('livingAreaValue'),
                'property_type': property_data.get('homeType', ''),
                'status': property_data.get('homeStatus', ''),
                'latitude': property_data.get('latitude'),
                'longitude': property_data.get('longitude'),
                'brokerage': (property_data.get('attributionInfo', {}).get('brokerName') or
                             property_data.get('brokerageName') or 
                             property_data.get('listingProvider', '')),
                'description': clean_text(property_data.get('description', '')),
                'year_built': property_data.get('yearBuilt'),
                'lot_size': property_data.get('lotSize'),
            }
        except Exception as e:
            logger.warning(f"Failed to parse property details: {e}")
            return None

    # ------------------------------------------------------------------
    # Property details by zpid — one fetch, many endpoints
    # ------------------------------------------------------------------

    def _get_property_data(self, zpid) -> Dict[str, Any]:
        """
        Fetch and cache the raw Zillow `property` object for a zpid.

        This is the single fetch that every detail endpoint (/property,
        /zestimate, /priceHistory, /taxHistory, /photos, /schools,
        /similarHomes) reads from. The parsed object is cached in Redis keyed by
        zpid, so only the first of those calls for a given zpid hits Zillow; the
        rest are served from cache.

        Raises NotFoundException if no property object is found and
        BlockedException if Zillow served a block/captcha page.
        """
        cache_key = f"property:{zpid}"
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(f"Property cache HIT for zpid {zpid}")
            return cached

        url = f"{self.BASE_URL}/homedetails/{zpid}_zpid/"
        soup = self.get_soup(url)

        title = soup.find('title')
        title_text = title.get_text().lower() if title else ''
        if 'denied' in title_text or 'blocked' in title_text or 'captcha' in title_text:
            logger.warning(f"Block detected for zpid {zpid}. Title: {title_text}")
            raise BlockedException("Request blocked by Zillow - access denied")

        script_data = extract_json_from_script(soup)
        property_data = {}

        if script_data:
            component_props = script_data.get('componentProps', {})
            gdp_cache = component_props.get('gdpClientCache', '')
            if isinstance(gdp_cache, str) and gdp_cache:
                try:
                    gdp_data = json.loads(gdp_cache)
                    for value in gdp_data.values():
                        if isinstance(value, dict) and value.get('property'):
                            property_data = value['property']
                            break
                except json.JSONDecodeError:
                    pass

            # Fallback to older flat structures.
            if not property_data:
                property_data = (
                    script_data.get('property', {}) or
                    script_data.get('propertyDetails', {}) or
                    {}
                )

        if not property_data:
            raise NotFoundException(f"No property found for zpid {zpid}")

        # Ensure the zpid is always present on the cached object.
        property_data.setdefault('zpid', self._coerce_int(zpid))

        cache.set(cache_key, property_data, PROPERTY_CACHE_TIMEOUT)
        logger.info(f"Cached property data for zpid {zpid}")
        return property_data

    @staticmethod
    def _coerce_int(value) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_address(data: Dict) -> str:
        """Build a 'street, city, state zip' address from a property-like dict."""
        addr = data.get('address')
        if isinstance(addr, dict):
            street = addr.get('streetAddress', '')
            city = addr.get('city', '')
            state = addr.get('state', '')
            zipcode = addr.get('zipcode', '')
        else:
            street = data.get('streetAddress', '')
            city = data.get('city', '')
            state = data.get('state', '')
            zipcode = data.get('zipcode', '')
        parts = [p for p in (street, city, state, zipcode) if p]
        if parts:
            return ', '.join(parts)
        return addr if isinstance(addr, str) else data.get('address', '') or ''

    @staticmethod
    def _photo_urls_from(photo_list) -> List[str]:
        """Extract the largest jpeg URL from a Zillow photo array."""
        urls = []
        for photo in photo_list or []:
            if isinstance(photo, dict):
                mixed = photo.get('mixedSources') or {}
                jpeg = mixed.get('jpeg') or []
                if jpeg:
                    urls.append(jpeg[-1].get('url', ''))
                elif photo.get('url'):
                    urls.append(photo.get('url'))
            elif isinstance(photo, str):
                urls.append(photo)
        return [u for u in urls if u]

    @staticmethod
    def _hoa_monthly(data: Dict) -> Optional[float]:
        """
        Return the HOA fee normalized to a monthly amount.

        `monthlyHoaFee` is already monthly and is preferred. The resoFacts
        fallback is a string carrying its own period ("$529 semi-annually"), so
        parsing the number alone would overstate the monthly cost 6x.
        """
        monthly = clean_price(data.get('monthlyHoaFee'))
        if monthly is not None:
            return monthly

        reso = data.get('resoFacts') or {}
        raw = reso.get('hoaFee') or reso.get('hoaFeeTotal')
        amount = clean_price(raw)
        if amount is None:
            return None

        text = str(raw).lower()
        # Longest label first: "annually" is a substring of "semi-annually", so
        # matching in dict order would divide a semi-annual fee by 12, not 6.
        for period in sorted(HOA_PERIOD_DIVISORS, key=len, reverse=True):
            if period in text:
                return round(amount / HOA_PERIOD_DIVISORS[period], 2)
        return amount

    def get_property_details(self, zpid) -> Dict[str, Any]:
        """Return a rich, flat details object for a single property."""
        data = self._get_property_data(zpid)

        price = clean_price(data.get('price'))
        sqft = data.get('livingArea') or data.get('livingAreaValue')
        photos = self._photo_urls_from(
            data.get('responsivePhotos') or data.get('photos') or
            data.get('hugePhotos') or data.get('originalPhotos')
        )
        reso = data.get('resoFacts') or {}
        attribution = data.get('attributionInfo') or {}

        price_per_sqft = None
        if price and sqft:
            try:
                price_per_sqft = round(price / float(sqft), 2)
            except (TypeError, ValueError, ZeroDivisionError):
                price_per_sqft = None

        return {
            'zpid': self._coerce_int(data.get('zpid') or zpid),
            'url': f"{self.BASE_URL}/homedetails/{data.get('zpid') or zpid}_zpid/",
            'address': self._build_address(data),
            'price': price,
            'zestimate': clean_price(data.get('zestimate')),
            'rent_zestimate': clean_price(data.get('rentZestimate')),
            'price_per_sqft': price_per_sqft,
            'beds': data.get('bedrooms') or data.get('beds'),
            'baths': data.get('bathrooms') or data.get('baths'),
            'sqft': self._coerce_int(sqft),
            'lot_size': data.get('lotSize') or data.get('lotAreaValue'),
            'year_built': data.get('yearBuilt'),
            'property_type': data.get('homeType', ''),
            'status': data.get('homeStatus', ''),
            'latitude': data.get('latitude'),
            'longitude': data.get('longitude'),
            'description': clean_text(data.get('description', '') or ''),
            'brokerage': (attribution.get('brokerName') or
                          data.get('brokerageName') or ''),
            'mls_id': attribution.get('mlsId') or reso.get('mlsId') or '',
            'mls_name': attribution.get('mlsName') or '',
            # resoFacts.hoaFee is a string carrying its own period ("$529
            # semi-annually") — normalize everything to a monthly figure.
            'hoa_fee': self._hoa_monthly(data),
            'days_on_zillow': data.get('daysOnZillow'),
            'page_view_count': data.get('pageViewCount'),
            'favorite_count': data.get('favoriteCount'),
            'photo_count': len(photos),
            'photo_url': photos[0] if photos else '',
        }

    def get_zestimate(self, zpid) -> Dict[str, Any]:
        """Return valuation estimates for a property."""
        data = self._get_property_data(zpid)
        return {
            'zpid': self._coerce_int(data.get('zpid') or zpid),
            'zestimate': clean_price(data.get('zestimate')),
            'rent_zestimate': clean_price(data.get('rentZestimate')),
            'price': clean_price(data.get('price')),
            'currency': data.get('currency', 'USD'),
        }

    def get_price_history(self, zpid) -> List[Dict[str, Any]]:
        """Return the list of price/listing events for a property."""
        data = self._get_property_data(zpid)
        events = []
        for item in data.get('priceHistory') or []:
            if not isinstance(item, dict):
                continue
            events.append({
                'date': item.get('date', ''),
                'event': item.get('event', ''),
                'price': clean_price(item.get('price')),
                'price_change_rate': item.get('priceChangeRate'),
                'price_per_sqft': clean_price(item.get('pricePerSquareFoot')),
                'source': item.get('source', ''),
            })
        return events

    def get_tax_history(self, zpid) -> List[Dict[str, Any]]:
        """Return the list of tax-assessment events for a property."""
        from datetime import datetime, timezone as dt_timezone

        data = self._get_property_data(zpid)
        events = []
        for item in data.get('taxHistory') or []:
            if not isinstance(item, dict):
                continue
            year = None
            epoch_ms = item.get('time')
            if epoch_ms:
                try:
                    year = datetime.fromtimestamp(
                        int(epoch_ms) / 1000, tz=dt_timezone.utc
                    ).year
                except (TypeError, ValueError, OverflowError, OSError):
                    year = None
            events.append({
                'year': year,
                'tax_paid': clean_price(item.get('taxPaid')),
                'tax_increase_rate': item.get('taxIncreaseRate'),
                'assessment': clean_price(item.get('value')),
                'assessment_increase_rate': item.get('valueIncreaseRate'),
            })
        return events

    def get_property_photos(self, zpid) -> List[str]:
        """Return all photo URLs for a property (largest available size)."""
        data = self._get_property_data(zpid)
        return self._photo_urls_from(
            data.get('responsivePhotos') or data.get('photos') or
            data.get('hugePhotos') or data.get('originalPhotos')
        )

    def get_schools(self, zpid) -> List[Dict[str, Any]]:
        """Return nearby/assigned schools for a property."""
        data = self._get_property_data(zpid)
        schools = []
        for item in data.get('schools') or []:
            if not isinstance(item, dict):
                continue
            schools.append({
                'name': item.get('name', ''),
                'rating': item.get('rating'),
                'level': item.get('level', ''),
                'grades': item.get('grades', ''),
                'distance': item.get('distance'),
                'type': item.get('type', ''),
                'link': item.get('link', ''),
            })
        return schools

    @staticmethod
    def _haversine_miles(lat1, lng1, lat2, lng2) -> Optional[float]:
        """Great-circle distance in miles, or None if either point is incomplete."""
        try:
            lat1, lng1, lat2, lng2 = float(lat1), float(lng1), float(lat2), float(lng2)
        except (TypeError, ValueError):
            return None
        r_lat1, r_lat2 = math.radians(lat1), math.radians(lat2)
        d_lat = r_lat2 - r_lat1
        d_lng = math.radians(lng2 - lng1)
        a = (math.sin(d_lat / 2) ** 2 +
             math.cos(r_lat1) * math.cos(r_lat2) * math.sin(d_lng / 2) ** 2)
        return round(EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a)), 3)

    @staticmethod
    def _list_type_for_status(status) -> str:
        """Pick the comp market that matches the subject's own listing status."""
        status = str(status or '').upper()
        if 'RENT' in status:
            return 'for-rent'
        if 'SOLD' in status:
            return 'sold'
        return 'for-sale'

    @staticmethod
    def _similarity_score(subject: Dict, card: Dict, distance_miles) -> float:
        """
        Score a candidate against the subject — lower is a closer comp.

        Every term is skipped when either side is missing rather than scored as
        zero, so a card with no sqft isn't rewarded for it.
        """
        w = SIMILARITY_WEIGHTS
        score = 0.0

        if distance_miles is not None:
            score += w['distance'] * distance_miles

        subject_sqft, card_sqft = subject.get('sqft'), card.get('sqft')
        if subject_sqft and card_sqft:
            score += w['sqft'] * abs(card_sqft - subject_sqft) / subject_sqft

        # Sold cards carry no sale price, so fall back to the zestimate — without
        # it every sold comp would score identically on the price term.
        subject_price = subject.get('price') or subject.get('zestimate')
        card_price = card.get('price') or card.get('zestimate')
        if subject_price and card_price:
            score += w['price'] * abs(card_price - subject_price) / subject_price

        for key, weight in (('beds', w['beds']), ('baths', w['baths'])):
            subject_value, card_value = subject.get(key), card.get(key)
            if subject_value is not None and card_value is not None:
                try:
                    score += weight * abs(float(card_value) - float(subject_value))
                except (TypeError, ValueError):
                    pass

        subject_type = subject.get('home_type') or ''
        card_type = str(card.get('property_type') or '').upper()
        if subject_type and card_type and subject_type != card_type:
            score += w['home_type']

        return round(score, 4)

    def _search_around(self, lat, lng, delta: float, list_type: str) -> List[Dict]:
        """
        Run one map-bounds search centred on a point, returning [] on failure.

        A degree of longitude narrows as latitude rises, so the east-west delta is
        scaled by 1/cos(lat) to keep the box roughly square on the ground rather
        than a thin sliver in the north.
        """
        lat, lng = float(lat), float(lng)
        lng_delta = delta / max(math.cos(math.radians(lat)), 0.1)
        try:
            parsed = self.search_by_map_bounds(
                north=lat + delta,
                south=lat - delta,
                east=lng + lng_delta,
                west=lng - lng_delta,
                list_type=list_type,
            )
        except (NotFoundException, ScraperException) as e:
            logger.info(f"Comp search found nothing within {delta} deg: {e}")
            return []
        return parsed.get('results') or []

    def get_similar_homes(
        self, zpid, count: int = 8, list_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Return comparable homes near a property, closest comp first.

        Zillow used to ship these on the homedetails `property` object as
        `nearbyHomes`; it no longer does, and its own comps API is behind a
        persisted-query safelist we can't call. So the list is rebuilt from the
        search index: search a tight box around the subject (widening once if
        that box is too sparse), drop the subject itself, then rank each
        candidate by distance, size, beds, baths, price and property type.

        `list_type` defaults to the subject's own market — comps for a sold home
        are other sold homes, comps for a rental are other rentals.

        Costs one search fetch on top of the (cached) subject fetch, so the
        result is cached separately under `similar:{zpid}:{list_type}:{count}`.
        """
        data = self._get_property_data(zpid)

        lat, lng = data.get('latitude'), data.get('longitude')
        if lat is None or lng is None:
            logger.warning(f"zpid {zpid} has no coordinates; cannot build comps")
            return []

        subject_zpid = self._coerce_int(data.get('zpid') or zpid)
        if list_type is None:
            list_type = self._list_type_for_status(data.get('homeStatus'))

        cache_key = f"similar:{subject_zpid}:{list_type}:{count}"
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(f"Similar-homes cache HIT for zpid {subject_zpid}")
            return cached

        subject = {
            'sqft': self._coerce_int(
                data.get('livingArea') or data.get('livingAreaValue')
            ),
            'beds': data.get('bedrooms'),
            'baths': data.get('bathrooms'),
            'price': clean_price(data.get('price')),
            'zestimate': clean_price(data.get('zestimate')),
            'home_type': str(data.get('homeType') or '').upper(),
        }

        # Tight box first; only pay for the wider search if it came back thin.
        candidates: Dict[int, Dict] = {}
        for delta in (SIMILAR_TIGHT_DELTA_DEG, SIMILAR_WIDE_DELTA_DEG):
            for card in self._search_around(lat, lng, delta, list_type):
                card_zpid = card.get('zpid')
                if card_zpid and card_zpid != subject_zpid:
                    candidates.setdefault(card_zpid, card)
            if len(candidates) >= count:
                break

        results = []
        for card in candidates.values():
            distance = self._haversine_miles(
                lat, lng, card.get('latitude'), card.get('longitude')
            )
            results.append({
                'zpid': card.get('zpid'),
                'address': card.get('address', ''),
                'url': card.get('url', ''),
                'photo_url': card.get('photo_url', ''),
                'price': card.get('price'),
                'beds': card.get('beds'),
                'baths': card.get('baths'),
                'sqft': card.get('sqft'),
                'property_type': card.get('property_type', ''),
                'status': card.get('status', ''),
                'latitude': card.get('latitude'),
                'longitude': card.get('longitude'),
                'brokerage': card.get('brokerage', ''),
                'zestimate': card.get('zestimate'),
                'date_sold': card.get('date_sold', ''),
                'distance_miles': distance,
                'similarity_score': self._similarity_score(subject, card, distance),
            })

        results.sort(key=lambda r: r['similarity_score'])
        results = results[:count]

        cache.set(cache_key, results, SIMILAR_HOMES_CACHE_TIMEOUT)
        logger.info(
            f"Built {len(results)} comps for zpid {subject_zpid} "
            f"({list_type}) from {len(candidates)} candidates"
        )
        return results

    # ------------------------------------------------------------------
    # Additional detail endpoints — all read the same cached property object
    # ------------------------------------------------------------------

    @staticmethod
    def _epoch_or_text(value) -> str:
        """Render a Zillow time value (epoch ms, epoch s, or string) as ISO/text."""
        from datetime import datetime, timezone as dt_timezone

        if value in (None, ''):
            return ''
        if isinstance(value, str):
            return value
        try:
            epoch = int(value)
        except (TypeError, ValueError):
            return str(value)
        # Zillow mixes seconds and milliseconds; anything this large is ms.
        if epoch > 10_000_000_000:
            epoch = epoch / 1000
        try:
            return datetime.fromtimestamp(
                epoch, tz=dt_timezone.utc
            ).isoformat().replace('+00:00', 'Z')
        except (ValueError, OverflowError, OSError):
            return str(value)

    def get_open_houses(self, zpid) -> List[Dict[str, Any]]:
        """
        Return the scheduled open houses for a property.

        `openHouseSchedule` is present on every listing but is an empty list for
        the large majority of them (only listings with an upcoming open house
        populate it). Key spellings differ between the detail page and the
        search cards, so every known variant is checked.
        """
        data = self._get_property_data(zpid)
        events = []
        for item in data.get('openHouseSchedule') or []:
            if not isinstance(item, dict):
                continue
            start = (item.get('startTime') or item.get('open_house_start') or
                     item.get('openHouseStartDate') or item.get('start'))
            end = (item.get('endTime') or item.get('open_house_end') or
                   item.get('openHouseEndDate') or item.get('end'))
            events.append({
                'start_time': self._epoch_or_text(start),
                'end_time': self._epoch_or_text(end),
                'description': clean_text(
                    item.get('description') or item.get('openHouseDescription') or ''
                ),
                'display_text': item.get('openHouse') or item.get('displayText') or '',
            })
        return events

    def get_listing_agent(self, zpid) -> Dict[str, Any]:
        """
        Return the listing agent, co-agent, broker and MLS attribution.

        `attributionInfo` is the authoritative source; `listedBy` is a
        display-oriented list of {id, elements:[{id, text}]} groups used as a
        fallback when attributionInfo omits a name or phone.
        """
        data = self._get_property_data(zpid)
        attribution = data.get('attributionInfo') or {}

        # Flatten listedBy into {GROUP_ID: {FIELD_ID: text}} for fallbacks.
        listed_by: Dict[str, Dict[str, str]] = {}
        for group in data.get('listedBy') or []:
            if not isinstance(group, dict) or not group.get('id'):
                continue
            fields = {}
            for element in group.get('elements') or []:
                if isinstance(element, dict) and element.get('id'):
                    fields[element['id']] = element.get('text') or ''
            listed_by[group['id']] = fields

        def fallback(group_id, field, default=''):
            return listed_by.get(group_id, {}).get(field) or default

        offices = [
            o.get('officeName', '') for o in attribution.get('listingOffices') or []
            if isinstance(o, dict) and o.get('officeName')
        ]

        return {
            'zpid': self._coerce_int(data.get('zpid') or zpid),
            'agent_name': attribution.get('agentName') or fallback('LISTING_AGENT', 'NAME'),
            'agent_phone': (attribution.get('agentPhoneNumber') or
                            fallback('LISTING_AGENT', 'PHONE')),
            'agent_email': attribution.get('agentEmail') or '',
            'agent_license': attribution.get('agentLicenseNumber') or '',
            'co_agent_name': attribution.get('coAgentName') or fallback('CO_LISTING_AGENT', 'NAME'),
            'co_agent_phone': (attribution.get('coAgentNumber') or
                               fallback('CO_LISTING_AGENT', 'PHONE')),
            'co_agent_license': attribution.get('coAgentLicenseNumber') or '',
            'broker_name': (attribution.get('brokerName') or
                            data.get('brokerageName') or fallback('BROKER', 'NAME')),
            'broker_phone': attribution.get('brokerPhoneNumber') or fallback('BROKER', 'PHONE'),
            'listing_offices': offices,
            'buyer_agent_name': attribution.get('buyerAgentName') or '',
            'buyer_brokerage_name': attribution.get('buyerBrokerageName') or '',
            'mls_id': attribution.get('mlsId') or data.get('mlsid') or '',
            'mls_name': attribution.get('mlsName') or '',
            'mls_disclaimer': clean_text(attribution.get('mlsDisclaimer') or ''),
            'attribution_contact': (attribution.get('listingAgentAttributionContact') or
                                    attribution.get('listingAttributionContact') or ''),
            'last_updated': attribution.get('lastUpdated') or '',
            'last_checked': attribution.get('lastChecked') or '',
        }

    def get_monthly_cost(self, zpid, down_payment_percent: float = 20.0,
                         term_years: int = 30,
                         interest_rate: Optional[float] = None) -> Dict[str, Any]:
        """
        Return an estimated monthly ownership cost breakdown.

        Principal & interest are amortized from the live Zillow mortgage rate
        (`mortgageZHLRates`) unless the caller supplies `interest_rate`. Taxes
        use the county rate Zillow ships on the listing, HOA is normalized to
        monthly, and insurance/PMI are estimated — Zillow ships neither.
        """
        data = self._get_property_data(zpid)

        price = clean_price(data.get('price'))
        rates = data.get('mortgageZHLRates') or {}
        bucket = ('fifteenYearFixedBucket' if int(term_years) == 15
                  else 'thirtyYearFixedBucket')
        if interest_rate is None:
            interest_rate = (rates.get(bucket) or {}).get('rate')

        down_payment = None
        loan_amount = None
        if price is not None:
            down_payment = round(price * (down_payment_percent / 100.0), 2)
            loan_amount = round(price - down_payment, 2)

        # Standard amortization; a 0% rate degrades to a straight-line payment.
        principal_and_interest = None
        if loan_amount and interest_rate is not None:
            months = int(term_years) * 12
            monthly_rate = float(interest_rate) / 100.0 / 12.0
            if monthly_rate > 0:
                factor = (1 + monthly_rate) ** -months
                principal_and_interest = round(
                    loan_amount * monthly_rate / (1 - factor), 2
                )
            elif months:
                principal_and_interest = round(loan_amount / months, 2)

        # Prefer the actual assessed tax bill; fall back to the county rate.
        reso = data.get('resoFacts') or {}
        annual_tax = clean_price(reso.get('taxAnnualAmount'))
        tax_rate = data.get('propertyTaxRate')
        if annual_tax is None and price is not None and tax_rate is not None:
            annual_tax = price * (float(tax_rate) / 100.0)
        property_tax = round(annual_tax / 12.0, 2) if annual_tax is not None else None

        insurance = (round(price * INSURANCE_RATE_ANNUAL / 12.0, 2)
                     if price is not None else None)
        hoa = self._hoa_monthly(data)

        pmi = None
        if loan_amount and down_payment_percent < 20:
            pmi = round(loan_amount * PMI_RATE_ANNUAL / 12.0, 2)

        components = [principal_and_interest, property_tax, insurance, hoa, pmi]
        total = round(sum(c for c in components if c), 2) if any(components) else None

        return {
            'zpid': self._coerce_int(data.get('zpid') or zpid),
            'price': price,
            'down_payment': down_payment,
            'down_payment_percent': down_payment_percent,
            'loan_amount': loan_amount,
            'interest_rate': interest_rate,
            'term_years': int(term_years),
            'rate_source': (rates.get(bucket) or {}).get('rateSource') or '',
            'principal_and_interest': principal_and_interest,
            'property_tax': property_tax,
            'property_tax_rate': tax_rate,
            'home_insurance': insurance,
            'hoa_fee': hoa,
            'mortgage_insurance': pmi,
            'total_monthly': total,
            'estimated_fields': ['home_insurance'] + (['mortgage_insurance'] if pmi else []),
            'currency': data.get('currency', 'USD'),
        }

    def get_home_facts(self, zpid) -> Dict[str, Any]:
        """
        Return the full RESO facts block for a property.

        Zillow ships ~187 resoFacts keys per listing, most of them null. Nulls
        and empty containers are dropped so callers get only populated fields.
        """
        data = self._get_property_data(zpid)
        reso = data.get('resoFacts') or {}

        facts = {
            key: value for key, value in reso.items()
            if value not in (None, '', [], {})
        }
        # atAGlanceFacts is a label/value list — surface it as a flat mapping too.
        at_a_glance = {
            item.get('factLabel'): item.get('factValue')
            for item in reso.get('atAGlanceFacts') or []
            if isinstance(item, dict) and item.get('factLabel')
        }

        return {
            'zpid': self._coerce_int(data.get('zpid') or zpid),
            'fact_count': len(facts),
            'at_a_glance': at_a_glance,
            'facts': facts,
        }

    def get_tax_assessment(self, zpid) -> Dict[str, Any]:
        """
        Return current tax assessment and parcel identifiers.

        Distinct from /taxHistory: this is the present-year assessment plus the
        county/parcel identifiers used to join against public records.
        """
        data = self._get_property_data(zpid)
        reso = data.get('resoFacts') or {}

        assessed = clean_price(reso.get('taxAssessedValue'))
        annual_tax = clean_price(reso.get('taxAnnualAmount'))
        effective_rate = None
        if assessed and annual_tax:
            effective_rate = round(annual_tax / assessed * 100, 3)

        return {
            'zpid': self._coerce_int(data.get('zpid') or zpid),
            'tax_assessed_value': assessed,
            'tax_annual_amount': annual_tax,
            'property_tax_rate': data.get('propertyTaxRate'),
            'effective_tax_rate': effective_rate,
            'parcel_id': data.get('parcelId') or reso.get('parcelNumber') or '',
            'county': data.get('county', ''),
            'county_fips': data.get('countyFIPS', ''),
            'zoning': reso.get('zoning') or '',
            'zoning_description': reso.get('zoningDescription') or '',
        }

    def get_nearby_areas(self, zpid) -> Dict[str, Any]:
        """
        Return the nearby cities, neighborhoods and zipcodes Zillow links to.

        Each entry carries the Zillow region path, which can be fed straight
        back into /bylocation or /byurl to widen a search.
        """
        data = self._get_property_data(zpid)

        def regions(key):
            out = []
            for item in data.get(key) or []:
                if not isinstance(item, dict) or not item.get('name'):
                    continue
                path = (item.get('regionUrl') or {}).get('path') or ''
                out.append({
                    'name': item['name'],
                    'path': path,
                    'url': f"{self.BASE_URL}{path}" if path else '',
                })
            return out

        return {
            'zpid': self._coerce_int(data.get('zpid') or zpid),
            'cities': regions('nearbyCities'),
            'neighborhoods': regions('nearbyNeighborhoods'),
            'zipcodes': regions('nearbyZipcodes'),
        }

    def get_listing_status(self, zpid) -> Dict[str, Any]:
        """
        Return listing status, price-cut tracking and listing-type flags.

        `listing_sub_type` carries the FSBO / foreclosure / auction / new-build
        / coming-soon / pending booleans that searches filter on, and
        `priceChange` is the signed delta of the most recent price move.
        """
        data = self._get_property_data(zpid)
        sub_type = data.get('listing_sub_type') or data.get('listingSubType') or {}

        price_change = clean_price(data.get('priceChange'))
        # clean_price strips the sign; recover it from the raw value.
        raw_change = data.get('priceChange')
        if price_change is not None and isinstance(raw_change, (int, float)) and raw_change < 0:
            price_change = -price_change

        return {
            'zpid': self._coerce_int(data.get('zpid') or zpid),
            'status': data.get('homeStatus', ''),
            'listing_type': data.get('listingTypeDimension', ''),
            'price': clean_price(data.get('price')),
            'price_change': price_change,
            'price_change_date': data.get('priceChangeDateString') or '',
            'date_sold': self._epoch_or_text(data.get('dateSold')),
            'last_sold_price': clean_price(data.get('lastSoldPrice')),
            'days_on_zillow': data.get('daysOnZillow'),
            'time_on_zillow': data.get('timeOnZillow', ''),
            'contingent_type': data.get('contingentListingType') or '',
            'is_fsbo': bool(sub_type.get('is_FSBO')),
            'is_fsba': bool(sub_type.get('is_FSBA')),
            'is_new_home': bool(sub_type.get('is_newHome')),
            'is_foreclosure': bool(sub_type.get('is_foreclosure')),
            'is_bank_owned': bool(sub_type.get('is_bankOwned')),
            'is_for_auction': bool(sub_type.get('is_forAuction')),
            'is_coming_soon': bool(sub_type.get('is_comingSoon')),
            'is_pending': bool(sub_type.get('is_pending')),
            'page_view_count': data.get('pageViewCount'),
            'favorite_count': data.get('favoriteCount'),
        }

    def search_by_address(self, address: str) -> Dict[str, Any]:
        """
        Resolve a street address to a single property's full details.

        Runs the address through the location search (which Zillow redirects to
        a /homedetails/ page for an exact match), takes the best-matching zpid,
        and returns the rich detail object from get_property_details — so callers
        with an address get the same payload as /property without needing a zpid.
        """
        if not address or not address.strip():
            raise NotFoundException("address is required")

        result = self.search_by_location(address)
        results = result.get('results') or []
        if not results:
            raise NotFoundException(f"No property found for address: {address}")

        zpid = results[0].get('zpid')
        if zpid:
            return self.get_property_details(zpid)

        # No zpid on the match (rare) — return the thin search card as-is; the
        # PropertyDetailsSerializer tolerates the missing fields.
        logger.info(f"Address '{address}' matched a result without a zpid; returning search card")
        return results[0]

    def get_apartment_details(self, url: str) -> Dict:
        """
        Get apartment/building details.
        
        Args:
            url: Apartment listing URL
            
        Returns:
            Apartment details dictionary
        """
        try:
            soup = self.get_soup(url)
            
            details = {
                'url': url,
                'name': '',
                'address': '',
                'description': '',
                'units': [],
                'amenities': [],
                'photos': [],
            }
            
            # Try script data - new structure: componentProps.initialReduxState.gdp.building
            script_data = extract_json_from_script(soup)
            if script_data:
                building = None
                
                # Try new structure
                component_props = script_data.get('componentProps', {})
                redux_state = component_props.get('initialReduxState', {})
                gdp = redux_state.get('gdp', {})
                if gdp:
                    building = gdp.get('building', {})
                
                # Fallback to old structure
                if not building:
                    building = script_data.get('building', {}) or script_data.get('property', {})
                
                if building:
                    # Build full address
                    street = building.get('streetAddress', '')
                    city = building.get('city', '')
                    state = building.get('state', '')
                    zipcode = building.get('zipcode', '')
                    full_address = building.get('fullAddress', '')
                    
                    if not full_address and street:
                        parts = [street]
                        if city:
                            parts.append(city)
                        if state:
                            parts.append(state)
                        if zipcode:
                            parts.append(zipcode)
                        full_address = ', '.join(parts)
                    
                    # Extract amenities from structuredAmenities
                    amenities = []
                    structured = building.get('structuredAmenities') or []
                    if structured:
                        for category in structured:
                            if isinstance(category, dict):
                                items = category.get('items') or []
                                for item in items:
                                    if isinstance(item, dict) and item.get('text'):
                                        amenities.append(item.get('text', ''))
                    
                    # Extract photos
                    photos = []
                    photo_list = building.get('photos') or building.get('galleryPhotos') or []
                    if photo_list:
                        for photo in photo_list:
                            if isinstance(photo, dict):
                                # Try to get URL from mixedSources
                                mixed = photo.get('mixedSources') or {}
                                jpeg = mixed.get('jpeg') or []
                                if jpeg and len(jpeg) > 0:
                                    photos.append(jpeg[-1].get('url', ''))  # Get largest
                                elif photo.get('url'):
                                    photos.append(photo.get('url'))
                    
                    # Extract floor plans / units
                    units = building.get('floorPlans') or building.get('ungroupedUnits') or []
                    
                    details.update({
                        'name': building.get('buildingName', '') or street,
                        'address': full_address,
                        'description': clean_text(building.get('description', '') or ''),
                        'units': units,
                        'amenities': amenities,
                        'photos': photos,
                    })
            
            # Fallback: Parse HTML
            if not details['name']:
                name_elem = soup.select_one('h1, [data-test="building-name"]')
                if name_elem:
                    details['name'] = clean_text(name_elem.get_text())
            
            if not details['address']:
                addr_elem = soup.select_one('[data-test="building-address"], address')
                if addr_elem:
                    details['address'] = clean_text(addr_elem.get_text())
            
            if not details['name']:
                raise NotFoundException(f"Apartment details not found: {url}")
            
            return details
            
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to get apartment details: {e}")
            raise ScraperException(f"Failed to get apartment details: {e}")
    
    # ------------------------------------------------------------------
    # Region market stats
    # ------------------------------------------------------------------

    @staticmethod
    def _round(value, digits=2) -> Optional[float]:
        """Round a numeric value, passing None through."""
        try:
            return round(float(value), digits)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _as_pct(cls, ratio, digits=2) -> Optional[float]:
        """Convert Zillow's 0-1 ratios to a percentage (0.0502 -> 5.02)."""
        value = cls._round(ratio, 10)
        return None if value is None else round(value * 100, digits)

    @classmethod
    def _series(cls, entries, value_key, digits=2) -> List[Dict[str, Any]]:
        """Flatten a Zillow {timePeriodEnd, <value_key>} range into date/value pairs."""
        series = []
        for entry in (entries or [])[:MARKET_HISTORY_LIMIT]:
            if not isinstance(entry, dict):
                continue
            value = cls._round(entry.get(value_key), digits)
            if value is None:
                continue
            series.append({'date': entry.get('timePeriodEnd', ''), 'value': value})
        return series

    @staticmethod
    def _region_ancestors(region: Dict) -> List[Dict[str, str]]:
        """
        Return only the regions that actually *contain* this one, widest first.

        Zillow's `parentage` array also lists the region's children (every
        zipcode inside a city), so returning it verbatim buries three useful
        ancestors under 73 descendants.
        """
        own_rank = REGION_HIERARCHY.get(
            str(region.get('regionTypeName') or '').lower()
        )
        ancestors = []
        for entry in region.get('parentage') or []:
            if not isinstance(entry, dict) or not entry.get('name'):
                continue
            entry_type = str(entry.get('regionType') or '').lower()
            entry_rank = REGION_HIERARCHY.get(entry_type)
            # Keep anything we can't rank — better an extra entry than a lost one.
            if own_rank is not None and entry_rank is not None and entry_rank >= own_rank:
                continue
            ancestors.append({'name': entry['name'], 'type': entry_type})
        return ancestors

    def _resolved_slug_candidates(self, location: str) -> List[str]:
        """
        Slug forms derived from /autocomplete, for when the literal slug misses.

        The home-values path is unforgiving about region slugs: `/austin-tx/`
        works but a bare zipcode does not — `/90210/home-values/` 404s while
        `/los-angeles-ca-90210/` resolves. Autocomplete knows the region's city,
        state and zip, which is enough to build the form Zillow wants.

        Only called after the literal slug fails, so the common case costs no
        extra request.
        """
        try:
            suggestions = self.autocomplete(location)
        except (ScraperException, NotFoundException) as e:
            logger.info(f"Autocomplete lookup for region {location!r} failed: {e}")
            return []

        for hit in suggestions:
            if hit.get('type') != 'region':
                continue
            city = slugify_location(hit.get('city') or '').lower()
            state = (hit.get('state') or '').lower()
            zipcode = hit.get('zipcode') or ''

            if (hit.get('region_type') or '') == 'zipcode' and zipcode:
                return [s for s in (
                    f"{city}-{state}-{zipcode}" if city and state else '',
                    f"{state}-{zipcode}" if state else '',
                ) if s]
            if city and state:
                return [f"{city}-{state}"]
            break  # only the top match is worth trying
        return []

    def _load_market_page(self, slug: str) -> Optional[Dict[str, Any]]:
        """Fetch one home-values page, returning its pageProps or None on a miss."""
        try:
            soup = self.get_soup(f"{self.BASE_URL}/{slug}/home-values/")
        except NotFoundException:
            logger.info(f"Market page miss for slug {slug!r}")
            return None

        node = soup.find('script', {'id': '__NEXT_DATA__'})
        if not node or not node.string:
            logger.info(f"Market page for {slug!r} carried no __NEXT_DATA__")
            return None
        try:
            data = json.loads(node.string)
        except json.JSONDecodeError:
            logger.warning(f"Market page for {slug!r} had unparseable __NEXT_DATA__")
            return None

        page_props = (data.get('props') or {}).get('pageProps') or {}
        if not page_props.get('odpMarketAnalytics'):
            logger.info(f"Market page for {slug!r} had no odpMarketAnalytics block")
            return None
        return page_props

    def _fetch_market_page(self, location: str):
        """
        Return (pageProps, slug) for the first slug form that resolves.

        Tries the caller's own slug first and only falls back to an autocomplete
        lookup if that misses, so a well-formed slug costs exactly one request.
        """
        literal = slugify_location(location).strip('/').lower()

        tried = set()
        if literal:
            tried.add(literal)
            page_props = self._load_market_page(literal)
            if page_props is not None:
                return page_props, literal

        for slug in self._resolved_slug_candidates(location):
            if slug in tried:
                continue
            tried.add(slug)
            page_props = self._load_market_page(slug)
            if page_props is not None:
                return page_props, slug

        raise NotFoundException(f"No market data found for location: {location}")

    def get_market_stats(self, location: str, history: bool = False) -> Dict[str, Any]:
        """
        Return market statistics for a region (city, zip, neighborhood, county,
        state).

        Sourced from the `odpMarketAnalytics` block on Zillow's home-values page
        for the region. Zillow publishes these monthly and staggers them, so the
        listing figures and the sale figures carry their own `as_of` dates rather
        than being forced onto a single one.

        Cached for six hours — these are monthly aggregates, so the 15-minute TTL
        used for listings would just buy repeated fetches of identical numbers.
        """
        cache_key = f"market:{slugify_location(location).lower()}:{int(bool(history))}"
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(f"Market stats cache HIT for {location!r}")
            return cached

        page_props, slug = self._fetch_market_page(location)
        market = page_props.get('odpMarketAnalytics') or {}

        # Both blocks describe the same region, but only `zhviRegion` carries
        # `parentage` — reading just `requestedRegion` silently loses it.
        region = dict(page_props.get('zhviRegion') or {})
        region.update({
            k: v for k, v in (page_props.get('requestedRegion') or {}).items()
            if v not in (None, '', [])
        })

        zhvi = market.get('zhviLatest') or {}
        listing = market.get('mrktListingLatest') or {}
        sale = market.get('mrktSaleLatest') or {}

        # medianDaysToPending and the sale-to-list figures are only ever on the
        # *Range arrays, never on the *Latest objects — read the newest entry.
        listing_range = market.get('mrktListingRange') or []
        sale_range = market.get('mrktSaleRange') or []
        rental_range = market.get('rentalMrktRange') or []
        latest_listing_period = listing_range[0] if listing_range else {}
        latest_sale_period = sale_range[0] if sale_range else {}
        latest_rental = rental_range[0] if rental_range else {}

        result = {
            'region': {
                'id': self._coerce_int(region.get('rid')),
                'name': region.get('name', ''),
                'type': region.get('regionTypeName', ''),
                'url': (
                    f"{self.BASE_URL}{(region.get('regionUrl') or {}).get('path', '')}"
                    if (region.get('regionUrl') or {}).get('path') else ''
                ),
                'slug': slug,
                'parentage': self._region_ancestors(region),
            },
            'listings_as_of': listing.get('timePeriodEnd', ''),
            'sales_as_of': sale.get('timePeriodEnd', ''),

            'home_value_index': self._round(zhvi.get('dataValue')),
            'home_value_index_yoy_pct': self._as_pct(zhvi.get('zhviYoY')),

            'median_list_price': self._round(listing.get('medianListPrice')),
            'median_sale_price': self._round(sale.get('medianSalePrice')),
            'median_days_to_pending': self._round(
                latest_listing_period.get('medianDaysToPending')
            ),
            # A ratio, not a percentage: 0.98 means homes sell for 98% of list.
            'median_sale_to_list_ratio': self._round(
                latest_sale_period.get('medianSaleToList'), 4
            ),
            'pct_sold_above_list': self._as_pct(
                latest_sale_period.get('pctSoldAboveList')
            ),
            'pct_sold_below_list': self._as_pct(
                latest_sale_period.get('pctSoldBelowList')
            ),
            'for_sale_inventory': self._round(listing.get('forSaleInventory')),
            'new_listings': self._round(listing.get('newListings')),
            'median_rent': self._round(latest_rental.get('zori')),

            # Zillow ships the same index for the parent county/state and the
            # nation, which is what makes a single region's number meaningful.
            'benchmarks': {
                'county_home_value_index': self._round(
                    (market.get('parentCountyZhviLatest') or {}).get('dataValue')
                ),
                'state_home_value_index': self._round(
                    (market.get('parentStateZhviLatest') or {}).get('dataValue')
                ),
                'national_home_value_index': self._round(
                    (market.get('nationalZhviLatest') or {}).get('dataValue')
                ),
                'national_median_rent': self._round(
                    ((market.get('nationalZori') or [{}])[0]).get('zori')
                ),
            },
        }

        if history:
            result['history'] = {
                'home_value_index': self._series(market.get('zhviRange'), 'dataValue'),
                'median_rent': self._series(rental_range, 'zori'),
                'median_days_to_pending': self._series(
                    listing_range, 'medianDaysToPending'
                ),
                'median_sale_to_list_ratio': self._series(
                    sale_range, 'medianSaleToList', 4
                ),
            }

        cache.set(cache_key, result, MARKET_STATS_CACHE_TIMEOUT)
        logger.info(
            f"Market stats for {region.get('name', location)!r} "
            f"({region.get('regionTypeName', '?')}) via slug {slug!r}"
        )
        return result

    def autocomplete(self, query: str) -> List[Dict]:
        """
        Get location and address autocomplete suggestions.

        Served by Zillow's public suggestions endpoint. The previous
        implementation POSTed a hand-written GraphQL query to `/zg-graph`, which
        answers anything outside its persisted-query safelist with
        QUERY_NOT_IN_SAFELIST — so it always failed and fell through to a stub
        that just title-cased the input and returned it as a city ("9021" came
        back as a place). That stub is gone: a query that matches nothing now
        returns an empty list.

        Region hits carry `region_id` (feed it back to a search), address hits
        carry `zpid` (feed it to any detail endpoint).
        """
        url = f"{self.AUTOCOMPLETE_URL}?{urlencode({'q': query})}"

        try:
            response = self.get(url)
            payload = response.json()
        except (ScraperException, ValueError, json.JSONDecodeError) as e:
            logger.error(f"Autocomplete request failed for {query!r}: {e}")
            raise ScraperException(f"Failed to fetch suggestions for: {query}")

        suggestions = []
        for result in payload.get('results') or []:
            meta = result.get('metaData') or {}
            result_type = str(result.get('resultType') or '').lower()
            suggestions.append({
                'display': result.get('display', ''),
                # Kept lowercase ('region'/'address') for continuity with the
                # shape this endpoint has always returned.
                'type': result_type,
                # `id` has always been the region id; addresses have none.
                'id': str(meta.get('regionId') or ''),
                'city': meta.get('city', ''),
                'state': meta.get('state', ''),
                'region_id': self._coerce_int(meta.get('regionId')),
                'region_type': meta.get('regionType', ''),
                'county': meta.get('county', ''),
                'zipcode': meta.get('zipCode', ''),
                'zpid': self._coerce_int(meta.get('zpid')),
                'address_type': meta.get('addressType', ''),
                'latitude': meta.get('lat'),
                'longitude': meta.get('lng'),
            })

        logger.info(f"Autocomplete for {query!r} returned {len(suggestions)} suggestions")
        return suggestions


# Singleton instance
property_scraper = PropertyScraper()
