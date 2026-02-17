"""
Utility functions for parsing Zillow pages.
"""

import re
import json
import logging
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def extract_json_from_script(soup: BeautifulSoup, pattern: str = None) -> Optional[Dict]:
    """
    Extract JSON data from script tags in Zillow pages.
    
    Zillow often embeds JSON data in script tags with specific IDs or patterns.
    
    Args:
        soup: BeautifulSoup object
        pattern: Optional regex pattern to match script content
        
    Returns:
        Parsed JSON data or None
    """
    # Try to find __NEXT_DATA__ script (common in Next.js pages)
    next_data = soup.find('script', {'id': '__NEXT_DATA__'})
    if next_data:
        try:
            data = json.loads(next_data.string)
            return data.get('props', {}).get('pageProps', {})
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"Failed to parse __NEXT_DATA__: {e}")
    
    # Try to find application/json script
    json_scripts = soup.find_all('script', {'type': 'application/json'})
    for script in json_scripts:
        try:
            if script.string:
                return json.loads(script.string)
        except json.JSONDecodeError:
            continue
    
    # Try to find embedded JS data
    if pattern:
        for script in soup.find_all('script'):
            if script.string and re.search(pattern, script.string):
                match = re.search(r'({.+})', script.string, re.DOTALL)
                if match:
                    try:
                        return json.loads(match.group(1))
                    except json.JSONDecodeError:
                        continue
    
    return None


def extract_apollo_state(soup: BeautifulSoup) -> Optional[Dict]:
    """
    Extract Apollo state data from Zillow pages.
    
    Zillow uses Apollo GraphQL client and stores state in the page.
    """
    for script in soup.find_all('script'):
        if script.string and 'apolloState' in script.string:
            try:
                match = re.search(r'"apolloState"\s*:\s*({.+?})\s*,\s*"[a-zA-Z]', script.string)
                if match:
                    return json.loads(match.group(1))
            except (json.JSONDecodeError, AttributeError):
                continue
    return None


def clean_price(price_str: str) -> Optional[float]:
    """
    Clean and parse a price string.
    
    Args:
        price_str: Price string like "$1,234,567" or "1234567"
        
    Returns:
        Float value or None if parsing fails
    """
    if not price_str:
        return None
    
    try:
        # Remove currency symbols, commas, and whitespace
        cleaned = re.sub(r'[^\d.]', '', str(price_str))
        return float(cleaned) if cleaned else None
    except (ValueError, TypeError):
        return None


def clean_number(num_str: str) -> Optional[int]:
    """
    Clean and parse a number string.
    
    Args:
        num_str: Number string like "3 beds" or "2,500 sqft"
        
    Returns:
        Integer value or None if parsing fails
    """
    if not num_str:
        return None
    
    try:
        # Extract first number from string
        match = re.search(r'[\d,]+', str(num_str))
        if match:
            cleaned = match.group().replace(',', '')
            return int(cleaned)
        return None
    except (ValueError, TypeError):
        return None


def clean_text(text: str) -> str:
    """Clean whitespace and normalize text, stripping HTML tags."""
    if not text:
        return ""
    # Strip HTML tags
    text = BeautifulSoup(str(text), "html.parser").get_text()
    return ' '.join(text.split())


def extract_zpid_from_url(url: str) -> Optional[int]:
    """
    Extract Zillow Property ID (zpid) from a URL.
    
    Args:
        url: Zillow property URL
        
    Returns:
        ZPID as integer or None
    """
    if not url:
        return None
    
    # Pattern: /homedetails/ADDRESS/ZPID_zpid/
    match = re.search(r'/(\d+)_zpid', url)
    if match:
        return int(match.group(1))
    
    # Pattern: zpid=ZPID
    match = re.search(r'zpid=(\d+)', url)
    if match:
        return int(match.group(1))
    
    return None


def parse_property_card(card_data: Dict) -> Optional[Dict]:
    """
    Parse property data from a Zillow listing card.
    
    Args:
        card_data: Dictionary containing property card data
        
    Returns:
        Normalized property dictionary
    """
    try:
        # Handle address - can be dict, string, or composed from street + city
        address = card_data.get('address') or card_data.get('streetAddress', '')
        if isinstance(address, dict):
            # Format address from dict (forSaleListings format)
            line1 = address.get('line1', '')
            line2 = address.get('line2', '')
            address = f"{line1}, {line2}" if line1 and line2 else line1 or line2
        elif not address:
            # pastSales format: street_address + city_state_zipcode
            street = card_data.get('street_address', '')
            city_state = card_data.get('city_state_zipcode', '')
            address = f"{street}, {city_state}" if street and city_state else street or city_state
        
        # Handle URL - may be relative or full, check multiple field names
        url = card_data.get('detailUrl') or card_data.get('listing_url') or card_data.get('home_details_url', '')
        if url and not url.startswith('http'):
            url = f"https://www.zillow.com{url}"
        
        # Handle photo URL - multiple possible field names
        photo_url = (card_data.get('primary_photo_url') or 
                    card_data.get('imgSrc') or 
                    card_data.get('image_url') or 
                    card_data.get('medium_image_url', ''))
        
        # Handle sqft - multiple possible field names
        sqft = (card_data.get('area') or 
               card_data.get('livingArea') or 
               card_data.get('livingAreaValue'))
        
        # Handle status - for sold properties, use sold_date
        status = (card_data.get('statusType') or 
                 card_data.get('status') or 
                 card_data.get('home_marketing_status') or
                 card_data.get('sold_date', ''))
        
        # Handle brokerage - multiple possible field names and nested structures
        brokerage = (card_data.get('brokerage_name') or
                    card_data.get('brokerName') or
                    card_data.get('brokerageName') or
                    card_data.get('listingProvider') or
                    '')
        
        # Check nested attributionInfo
        if not brokerage:
            attribution = card_data.get('attributionInfo', {})
            if isinstance(attribution, dict):
                brokerage = (attribution.get('brokerName') or 
                            attribution.get('agentName') or
                            attribution.get('listingOffice', ''))
        
        return {
            'zpid': card_data.get('zpid') or extract_zpid_from_url(url),
            'address': address,
            'url': url,
            'photo_url': photo_url,
            'price': clean_price(card_data.get('price') or card_data.get('unformattedPrice')),
            'beds': card_data.get('beds') or card_data.get('bedrooms'),
            'baths': card_data.get('baths') or card_data.get('bathrooms'),
            'sqft': sqft,
            'property_type': card_data.get('propertyType') or card_data.get('home_type', ''),
            'status': status,
            'latitude': card_data.get('latitude'),
            'longitude': card_data.get('longitude'),
            'brokerage': brokerage,
        }
    except Exception as e:
        logger.warning(f"Failed to parse property card: {e}")
        return None


def parse_agent_card(card_data: Dict) -> Optional[Dict]:
    """
    Parse agent data from a Zillow agent card.
    
    Args:
        card_data: Dictionary containing agent card data
        
    Returns:
        Normalized agent dictionary
    """
    try:
        name = card_data.get('fullName') or card_data.get('name', '')
        screenname = card_data.get('screenName', '')
        
        return {
            'name': name,
            'url': f"https://www.zillow.com/profile/{screenname}" if screenname else '',
            'location': card_data.get('location', ''),
            'phone': card_data.get('phone', ''),
            'rating': card_data.get('avgRating'),
            'reviews_count': card_data.get('numReviews'),
            'sales_count': card_data.get('salesLast12Months'),
            'photo_url': card_data.get('profilePhotoSrc', ''),
        }
    except Exception as e:
        logger.warning(f"Failed to parse agent card: {e}")
        return None


def parse_review(review_data: Dict) -> Optional[Dict]:
    """
    Parse review data from Zillow.
    
    Args:
        review_data: Dictionary containing review data
        
    Returns:
        Normalized review dictionary
    """
    try:
        # Extract reviewer info which might be nested
        reviewer = review_data.get('reviewer', {})
        reviewer_name = review_data.get('reviewerName') or review_data.get('subHeader') or reviewer.get('screenName', '')
        reviewer_zuid = review_data.get('reviewerZuid') or reviewer.get('encodedZuid', '')
        
        return {
            'zuid': reviewer_zuid,
            'rating': review_data.get('rating') or review_data.get('overallRating', 0),
            'review': clean_text(review_data.get('reviewText') or review_data.get('reviewComment', '')),
            'reviewer_name': reviewer_name,
            'date': review_data.get('createDate') or review_data.get('date', ''),
            'transaction_type': review_data.get('transactionType') or review_data.get('workType') or review_data.get('workDescription', ''),
        }
    except Exception as e:
        logger.warning(f"Failed to parse review: {e}")
        return None



def slugify_location(location: str) -> str:
    """
    Convert a free-form location string into a Zillow-compatible URL slug.
    
    Examples:
        "35 Morse Ave Bloomfield, NJ 07003" -> "35-Morse-Ave-Bloomfield-NJ-07003"
        "Bloomfield NJ" -> "Bloomfield-NJ"
        "seattle-wa" -> "seattle-wa" (already slugified)
        "Los Angeles, CA" -> "Los-Angeles-CA"
    """
    if not location:
        return location
    
    # Remove commas, periods, and hash signs
    slug = location.replace(',', '').replace('.', '').replace('#', '')
    # Replace spaces with hyphens
    slug = slug.replace(' ', '-')
    # Collapse multiple hyphens
    while '--' in slug:
        slug = slug.replace('--', '-')
    # Strip leading/trailing hyphens
    slug = slug.strip('-')
    
    return slug


def extract_broad_location(location: str) -> str:
    """
    Extract a broader location (city + state) from a full address string.
    
    Examples:
        "35 Morse Ave Bloomfield, NJ 07003" -> "Bloomfield-NJ"
        "123 Main St, Los Angeles, CA 90001" -> "Los-Angeles-CA"
        "Bloomfield NJ" -> "Bloomfield-NJ" (returned as-is, slugified)
    
    Heuristic: uses comma-splitting and street suffix detection to isolate
    the city name from the street address.
    """
    import re
    
    # Common US street suffixes (used to detect where street name ends and city begins)
    STREET_SUFFIXES = {
        'ave', 'avenue', 'blvd', 'boulevard', 'cir', 'circle', 'ct', 'court',
        'dr', 'drive', 'hwy', 'highway', 'ln', 'lane', 'loop', 'pass',
        'path', 'pike', 'pl', 'place', 'rd', 'road', 'run', 'sq', 'square',
        'st', 'street', 'ter', 'terrace', 'trl', 'trail', 'way', 'xing',
    }
    
    # Common US state abbreviations
    state_pattern = r'\b([A-Z]{2})\b'
    
    # Try to find state abbreviation
    match = re.search(state_pattern, location)
    if match:
        state = match.group(1)
        # Get everything before the state
        before_state = location[:match.start()].strip().rstrip(',').strip()
        
        # Split by comma to find city
        parts = [p.strip() for p in before_state.split(',')]
        
        if len(parts) >= 2:
            # Multi-comma format: "123 Main St, Los Angeles, CA 90001"
            # The last comma-separated part before state is the city
            city = parts[-1]
            # If city still has a number prefix (unlikely but possible), strip it
            if city and city[0].isdigit():
                city = parts[-1]
        else:
            # Single part: "35 Morse Ave Bloomfield" (street + city combined)
            # Find the last street suffix and take everything AFTER it as the city
            words = parts[0].split()
            city_start_idx = 0
            
            for i, w in enumerate(words):
                if w.lower().rstrip('.') in STREET_SUFFIXES:
                    city_start_idx = i + 1  # City starts AFTER the suffix
            
            if city_start_idx > 0 and city_start_idx < len(words):
                city = ' '.join(words[city_start_idx:])
            elif words and words[0][0:1].isdigit():
                # No street suffix found but starts with number - take last word(s) as city
                # Skip the initial number
                non_number_words = []
                for w in reversed(words):
                    if w[0].isdigit():
                        break
                    non_number_words.insert(0, w)
                city = ' '.join(non_number_words) if non_number_words else parts[0]
            else:
                city = parts[0]
        
        if city:
            return slugify_location(f"{city}, {state}")
    
    # Fallback: just slugify the whole thing
    return slugify_location(location)


def build_search_url(
    location: str = None,
    list_type: str = 'for-sale',
    page: int = 1,
    **filters
) -> str:
    """
    Build a Zillow search URL with filters.
    
    Args:
        location: Location string (e.g., "seattle-wa" or "35 Morse Ave Bloomfield, NJ 07003")
        list_type: Type of listing (for-sale, for-rent, sold)
        page: Page number
        **filters: Additional filters
        
    Returns:
        Formatted search URL
    """
    base = "https://www.zillow.com"
    
    if location:
        slug = slugify_location(location)
        path = f"/{slug}/"
    else:
        path = "/homes/"
    
    if list_type == 'for-rent':
        path += "rentals/"
    elif list_type == 'sold':
        path += "sold/"
    
    # Add pagination
    if page > 1:
        path += f"{page}_p/"
    
    return base + path
