"""
Property scraper for Zillow property listings.
"""

import re
import json
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
            # resoFacts.hoaFee is often a string like "$50 monthly" — normalize.
            'hoa_fee': clean_price(reso.get('hoaFee') or data.get('monthlyHoaFee')),
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

    def get_similar_homes(self, zpid) -> List[Dict[str, Any]]:
        """Return comparable / nearby homes as property cards."""
        data = self._get_property_data(zpid)
        homes = data.get('nearbyHomes') or data.get('comps') or []
        results = []
        for home in homes:
            if not isinstance(home, dict) or not home.get('zpid'):
                continue
            photos = self._photo_urls_from(
                home.get('miniCardPhotos') or home.get('photos')
            )
            results.append({
                'zpid': self._coerce_int(home.get('zpid')),
                'address': self._build_address(home),
                'url': f"{self.BASE_URL}/homedetails/{home.get('zpid')}_zpid/",
                'photo_url': photos[0] if photos else '',
                'price': clean_price(home.get('price')),
                'beds': home.get('bedrooms') or home.get('beds'),
                'baths': home.get('bathrooms') or home.get('baths'),
                'sqft': self._coerce_int(home.get('livingArea')),
                'property_type': home.get('homeType', ''),
                'status': home.get('homeStatus', ''),
                'latitude': home.get('latitude'),
                'longitude': home.get('longitude'),
            })
        return results

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
    
    def autocomplete(self, query: str) -> List[Dict]:
        """
        Get location autocomplete suggestions.
        
        Args:
            query: Search query
            
        Returns:
            List of suggestion dictionaries
        """
        # Zillow's autocomplete API - requires specific headers
        url = "https://www.zillow.com/zg-graph"
        
        # GraphQL query for autocomplete
        payload = {
            "query": """
                query getAutoCompleteResults($query: String!) {
                    zgsAutocompleteRequest(query: $query) {
                        results {
                            display
                            resultType
                            metaData {
                                regionId
                                regionType
                                city
                                state
                                county
                            }
                        }
                    }
                }
            """,
            "variables": {"query": query}
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Referer': 'https://www.zillow.com/',
            'Origin': 'https://www.zillow.com',
        }
        
        try:
            import requests
            
            # Make direct request with proper headers
            response = requests.post(
                url,
                json=payload,
                headers={**self._get_headers(), **headers},
                timeout=self.timeout
            )
            
            if response.status_code != 200:
                # Fallback: Use simple search redirect approach
                return self._autocomplete_fallback(query)
            
            data = response.json()
            
            results = data.get('data', {}).get('zgsAutocompleteRequest', {}).get('results', [])
            
            suggestions = []
            for result in results:
                meta = result.get('metaData', {}) or {}
                suggestions.append({
                    'display': result.get('display', ''),
                    'type': result.get('resultType', ''),
                    'id': meta.get('regionId', ''),
                    'city': meta.get('city', ''),
                    'state': meta.get('state', ''),
                })
            
            if not suggestions:
                return self._autocomplete_fallback(query)
            
            return suggestions
            
        except Exception as e:
            logger.error(f"GraphQL autocomplete failed: {e}, trying fallback")
            return self._autocomplete_fallback(query)
    
    def _autocomplete_fallback(self, query: str) -> List[Dict]:
        """Fallback autocomplete using search page parsing."""
        try:
            # Try to search and extract suggestions from the page
            search_url = f"{self.BASE_URL}/homes/{query.replace(' ', '-')}_rb/"
            
            soup = self.get_soup(search_url)
            
            # Return a simple suggestion based on the query
            return [{
                'display': query.title(),
                'type': 'search',
                'id': '',
                'city': query.title(),
                'state': '',
            }]
            
        except Exception as e:
            logger.error(f"Autocomplete fallback failed: {e}")
            raise NotFoundException(f"No suggestions found for: {query}")


# Singleton instance
property_scraper = PropertyScraper()
