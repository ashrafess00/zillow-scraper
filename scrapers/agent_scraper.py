"""
Agent scraper for Zillow agent data.
"""

import re
import json
import html
import logging
from typing import Optional, Dict, List, Any
from urllib.parse import quote

from django.core.cache import cache

from .base import BaseScraper, NotFoundException, ScraperException
from .utils import (
    extract_json_from_script,
    parse_agent_card,
    parse_property_card,
    parse_review,
    clean_text,
)

logger = logging.getLogger(__name__)

# ZUID cache timeout: 30 days (ZUIDs are permanent identifiers)
ZUID_CACHE_TIMEOUT = 60 * 60 * 24 * 30


class AgentScraper(BaseScraper):
    """Scraper for Zillow agent data."""
    
    def get_agents_by_location(self, location: str, page: int = 1) -> Dict[str, Any]:
        """
        Get agents by location.
        
        Args:
            location: Location slug (e.g., "los-angeles")
            page: Page number
            
        Returns:
            Dict with 'results' (list), 'total_results', and 'current_page' (inside 'results' key? No, top level)
            Wait, I'll return Dict but caller expects specific structure. 
            The view expects me to return Dict.
        """
        url = f"{self.BASE_URL}/professionals/real-estate-agent-reviews/{location}/"
        if page > 1:
            url = f"{url}?page={page}"
            
        try:
            soup = self.get_soup(url)
            agents_result = {'results': [], 'total_results': 0, 'current_page': page}
            
            # Debug: Log page title to verify we got the right page
            title = soup.find('title')
            logger.info(f"Page title: {title.get_text() if title else 'No title'}")
            
            # Try to find JSON data in script tags
            for script in soup.find_all('script'):
                script_text = script.string or ''
                
                # Skip empty or short scripts
                if len(script_text) < 1000:
                    continue
                
                # Try to parse as JSON directly (some scripts are pure JSON)
                if script_text.strip().startswith('{'):
                    try:
                        data = json.loads(script_text)
                        extracted = self._extract_agents_from_json(data, location)
                        
                        # Handle if extracted is list (old) or dict (new)
                        # We will update _extract_agents_from_json next
                        # For now assume it might return list, we wrap it
                        if isinstance(extracted, list):
                             if extracted:
                                agents_result['results'] = extracted
                                agents_result['total_results'] = len(extracted)
                                return agents_result
                        elif isinstance(extracted, dict):
                             if extracted.get('results'):
                                 if extracted.get('total_results', 0) == 0:
                                     extracted['total_results'] = len(extracted['results'])
                                 return extracted
                    except json.JSONDecodeError:
                        pass
                
                # Look for Next.js style pageProps
                if '"pageProps"' in script_text or '"searchResults"' in script_text:
                    try:
                        data = json.loads(script_text)
                        extracted = self._extract_agents_from_json(data, location)
                        
                        if isinstance(extracted, list):
                             if extracted:
                                agents_result['results'] = extracted
                                agents_result['total_results'] = len(extracted)
                                return agents_result
                        elif isinstance(extracted, dict):
                             if extracted.get('results'):
                                 if extracted.get('total_results', 0) == 0:
                                     extracted['total_results'] = len(extracted['results'])
                                 return extracted
                    except json.JSONDecodeError:
                        pass
            
            # Fallback: Parse profile links directly from HTML
            # Fallback: Parse profile links directly from HTML
            if not agents_result['results']:
                logger.info("No agents found in scripts, parsing profile links...")
                profile_links = soup.select('a[href*="/profile/"]')
                
                seen_slugs = set()
                for link in profile_links:
                    href = link.get('href', '')
                    if '/profile/' in href:
                        # Extract agent slug from URL
                        name_match = re.search(r'/profile/([^/]+)', href)
                        if name_match:
                            agent_slug = name_match.group(1)
                            
                            # Skip if already seen
                            if agent_slug in seen_slugs:
                                continue
                            seen_slugs.add(agent_slug)
                            
                            # Use slug as name (convert to title case)
                            name = agent_slug.replace('-', ' ').title()
                            full_url = href if href.startswith('http') else f"{self.BASE_URL}{href}"
                            
                            agents_result['results'].append({
                                'name': name,
                                'url': full_url,
                                'photo_url': '',
                                'brokerage': '',
                                'location': location.replace('-', ' ').title(),
                                'rating': None,
                                'reviews_count': None,
                                'sales_count': None,
                                'price_range': None,
                                'is_team': False,
                            })
                
                logger.info(f"Found {len(agents_result['results'])} agents from profile links")
            
            if not agents_result['results']:
                raise NotFoundException(f"No agents found for location: {location}")
            
            return {
                'source_url': url,
                'results': agents_result['results'],
                'total_results': agents_result.get('total_results', len(agents_result['results'])),
                'current_page': page
            }
            
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to get agents by location: {e}")
            raise ScraperException(f"Failed to get agents for {location}: {e}")
    
    def _extract_agents_from_json(self, data: Dict, location: str) -> Dict[str, Any]:
        """Extract agents from various JSON structures."""
        agents = []
        total_results = 0
        current_page = 1
        
        # Try to navigate to searchResults first
        search_results = None
        try:
            # Path from debug output: props.pageProps.displayData.agentDirectoryFinderDisplay.searchResults
            page_props = data.get('props', {}).get('pageProps', {})
            display_data = page_props.get('displayData', {})
            agent_display = display_data.get('agentDirectoryFinderDisplay', {})
            
            # Extract metadata
            total_results = agent_display.get('totalResultCount', 0)
            current_page = agent_display.get('currentPage', 1)
            
            search_results = agent_display.get('searchResults', {})
            
            # If total_results is 0, try to get it from search_results['resultsFound']
            if total_results == 0 and search_results:
                total_results = search_results.get('resultsFound', 0)
                
            if search_results:
                logger.info(f"Found searchResults with keys: {list(search_results.keys())[:10]}")
                # Update current_page from search_results if available
                if search_results.get('currentPage'):
                    current_page = search_results.get('currentPage')
        except Exception as e:
            logger.debug(f"Path navigation failed: {e}")
        
        # Try different keys for the agent list
        agent_list_keys = ['results', 'professionals', 'agents', 'items', 'listResults', 'professionalCards']
        
        if search_results:
            for key in agent_list_keys:
                agent_list = search_results.get(key)
                if agent_list:
                    # If it's a dict, look for a list inside it
                    if isinstance(agent_list, dict):
                        logger.info(f"Key '{key}' is a dict with keys: {list(agent_list.keys())[:10]}")
                        # Try common list keys inside the dict
                        for subkey in ['resultsCards', 'professionalCards', 'cards', 'professionals', 'items', 'list', 'data']:
                            sublist = agent_list.get(subkey)
                            if isinstance(sublist, list) and len(sublist) > 0:
                                logger.info(f"Found {len(sublist)} items under '{key}.{subkey}'")
                                if isinstance(sublist[0], dict):
                                    logger.info(f"First item keys: {list(sublist[0].keys())[:15]}")
                                for agent_data in sublist:
                                    parsed = self._parse_agent_from_json(agent_data, location)
                                    if parsed:
                                        agents.append(parsed)
                                if agents:
                                    return {
                                        'results': agents,
                                        'total_results': total_results,
                                        'current_page': current_page
                                    }
                    elif isinstance(agent_list, list) and len(agent_list) > 0:
                        logger.info(f"Found {len(agent_list)} items under '{key}'")
                        if isinstance(agent_list[0], dict):
                            logger.info(f"First item keys: {list(agent_list[0].keys())[:15]}")
                        for agent_data in agent_list:
                            parsed = self._parse_agent_from_json(agent_data, location)
                            if parsed:
                                agents.append(parsed)
                        if agents:
                            return {
                                'results': agents,
                                'total_results': total_results,
                                'current_page': current_page
                            }
        
        # Try other top-level paths
        paths_to_try = [
            # Direct access
            lambda d: d.get('searchResults', {}).get('professionals', []),
            lambda d: d.get('professionals', []),
            lambda d: d.get('agents', []),
            lambda d: d.get('results', []),
        ]
        
        for path_func in paths_to_try:
            try:
                results = path_func(data)
                if results and isinstance(results, list):
                    for agent_data in results:
                        parsed = self._parse_agent_from_json(agent_data, location)
                        if parsed:
                            agents.append(parsed)
                    if agents:
                        return {
                            'results': agents,
                            'total_results': total_results,
                            'current_page': current_page
                        }
            except (KeyError, TypeError, AttributeError):
                continue
        
        return {
            'results': agents,
            'total_results': total_results,
            'current_page': current_page
        }
    
    def _parse_agent_from_json(self, agent_data: Dict, location: str) -> Optional[Dict]:
        """Parse a single agent from JSON data."""
        if not isinstance(agent_data, dict):
            return None
        
        # Handle Zillow's resultsCards structure
        # Keys: cardTitle, encodedZuid, reviewInformation, profileData, cardActionLink
        
        # Try various field names for name
        name = (
            agent_data.get('cardTitle') or
            agent_data.get('fullName') or
            agent_data.get('displayName') or 
            agent_data.get('name') or
            agent_data.get('businessName') or
            ''
        )
        
        if not name:
            return None
        
        # Filter out non-agent entries
        if 'help finding' in name.lower() or 'get help' in name.lower():
            return None
        
        # Build profile URL
        screen_name = agent_data.get('encodedZuid') or agent_data.get('screenName') or ''
        profile_url = agent_data.get('cardActionLink') or agent_data.get('profileLink') or agent_data.get('profileUrl') or ''
        
        # Only accept profile URLs
        if profile_url and '/profile/' not in profile_url:
            return None
        
        if not profile_url and screen_name:
            profile_url = f"{self.BASE_URL}/profile/{screen_name}/"
        
        # Extract review information (ensure it's a dict)
        review_info = agent_data.get('reviewInformation')
        if not isinstance(review_info, dict):
            review_info = {}
        
        # Rating: use reviewAverage (number) or reviewAverageText (string like "5.0")
        rating = review_info.get('reviewAverage') or review_info.get('rating') or agent_data.get('avgRating')
        if not rating and review_info.get('reviewAverageText'):
            try:
                rating = float(review_info.get('reviewAverageText', '0'))
            except (ValueError, TypeError):
                pass
        
        # Reviews count: use reviewCountText (string like "(1595)") and extract number
        reviews_count = None
        review_count_text = review_info.get('reviewCountText', '')
        if review_count_text:
            # Extract number from "(1595)" format
            count_match = re.search(r'(\d+)', review_count_text)
            if count_match:
                reviews_count = int(count_match.group(1))
        if not reviews_count:
            reviews_count = review_info.get('reviewCount') or agent_data.get('numReviews')
        
        # Extract profile data for additional info
        # Note: profileData might be a list of stats, not a dict
        profile_data = agent_data.get('profileData')
        sales_count = None
        
        # Debug: log profileData and tags structure
        if profile_data:
            logger.debug(f"profileData type: {type(profile_data).__name__}, content: {str(profile_data)[:300]}")
        tags = agent_data.get('tags', [])
        if tags:
            logger.debug(f"tags content: {str(tags)[:300]}")
        
        if isinstance(profile_data, dict):
            sales_count = profile_data.get('salesLast12Months') or profile_data.get('recentSalesCount')
        elif isinstance(profile_data, list):
            # It's a list of stat items - look for sales info
            # Structure: {'data': '211', 'label': 'team sales last 12 months'}
            for item in profile_data:
                if isinstance(item, dict):
                    label = item.get('label', '').lower()
                    data = item.get('data')
                    # Look for "sales last 12 months" or "team sales last 12 months"
                    if 'sales' in label and '12' in label:
                        if data:
                            try:
                                sales_count = int(re.sub(r'[^\d]', '', str(data)))
                            except (ValueError, TypeError):
                                pass
                        break
        
        # Also check tags for sales info
        if not sales_count:
            tags = agent_data.get('tags', [])
            for tag in tags:
                if isinstance(tag, dict):
                    text = tag.get('text', '').lower()
                    if 'sales' in text:
                        # Extract number from text like "271 team sales last 12 months"
                        match = re.search(r'(\d+)\s*(?:team\s+)?sales', text)
                        if match:
                            sales_count = int(match.group(1))
                            break
        
        # Get location (handle profile_data being a list)
        agent_location = agent_data.get('location') or location.replace('-', ' ').title()
        if isinstance(profile_data, dict) and profile_data.get('location'):
            agent_location = profile_data.get('location')
        
        # Get brokerage from secondaryCardTitle (e.g., "RE/Max ONE")
        brokerage = agent_data.get('secondaryCardTitle', '')
        
        # Check if this is a team
        is_team = False
        tags = agent_data.get('tags', [])
        for tag in tags:
            if isinstance(tag, dict) and tag.get('text', '').upper() == 'TEAM':
                is_team = True
                break
        
        # Get price range from profileData
        price_range = None
        if isinstance(profile_data, list):
            for item in profile_data:
                if isinstance(item, dict):
                    label = item.get('label', '').lower()
                    if 'price range' in label:
                        price_range = item.get('data')
                        break
        
        # Get photo URL
        photo_url = agent_data.get('imageUrl') or agent_data.get('logoUrl', '')
        
        return {
            'name': name,
            'url': profile_url,
            'photo_url': photo_url,
            'brokerage': brokerage,
            'location': agent_location,
            'rating': float(rating) if rating else None,
            'reviews_count': int(reviews_count) if reviews_count else None,
            'sales_count': int(sales_count) if sales_count else None,
            'price_range': price_range,
            'is_team': is_team,
        }
    
    def get_agent_info(self, agentname: str = None, url: str = None) -> Dict:
        """
        Get agent profile information.
        
        Args:
            agentname: Agent screen name
            url: Direct profile URL
            
        Returns:
            Agent profile dictionary
        """
        if url:
            profile_url = url
        elif agentname:
            profile_url = f"{self.BASE_URL}/profile/{agentname}/"
        else:
            raise ValueError("Either agentname or url must be provided")
        
        try:
            soup = self.get_soup(profile_url)
            
            # Extract profile data from page
            profile = {
                'name': '',
                'url': profile_url,
                'photo_url': '',
                'brokerage': '',
                'location': '',
                'phone': '',
                'rating': None,
                'reviews_count': None,
                'sales_count': None,
                'sales_last_12_months': None,
                'total_sales': None,
                'price_range': '',
                'is_team': False,
                'bio': '',
            }
            
            # Try to find JSON-LD data (reliable source for basics)
            ld_json_script = soup.find('script', type='application/ld+json')
            if ld_json_script and ld_json_script.string:
                try:
                    # JSON-LD content in Zillow is often HTML escaped
                    json_str = html.unescape(ld_json_script.string)
                    ld_data = json.loads(json_str)
                    # Handle if it's a list of LD objects
                    if isinstance(ld_data, list):
                        ld_data = ld_data[0]
                        
                    profile['photo_url'] = ld_data.get('image', '')
                    profile['phone'] = ld_data.get('telephone', '')
                    profile['name'] = ld_data.get('name', profile['name'])
                    # Bio often contains HTML in description
                    profile['bio'] = clean_text(ld_data.get('description', ''))
                    
                    if 'address' in ld_data:
                        addr = ld_data['address']
                        if isinstance(addr, dict):
                             parts = [
                                 addr.get('addressLocality'),
                                 addr.get('addressRegion')
                             ]
                             profile['location'] = ', '.join(filter(None, parts))
                    
                    if 'aggregateRating' in ld_data:
                        rating_data = ld_data['aggregateRating']
                        profile['rating'] = float(rating_data.get('ratingValue', 0))
                        profile['reviews_count'] = int(rating_data.get('ratingCount', 0))
                except Exception as e:
                    logger.warning(f"Failed to parse JSON-LD: {e}")

            # Try to find JSON data in script tags
            for script in soup.find_all('script'):
                script_text = script.string or ''
                if len(script_text) > 1000 and ('"pageProps"' in script_text or '"professional"' in script_text):
                    try:
                        data = json.loads(script_text)
                        # Navigate to professional data
                        page_props = data.get('props', {}).get('pageProps', {})
                        prof_data = page_props.get('displayData', {}).get('professionalDataByScreenName', {})
                        
                        if prof_data:
                            profile.update({
                                'name': prof_data.get('fullName') or prof_data.get('displayName', ''),
                                'phone': prof_data.get('phone', ''),
                                'brokerage': prof_data.get('brokerageName') or prof_data.get('businessName', ''),
                                'rating': prof_data.get('avgRating') or prof_data.get('rating'),
                                'reviews_count': prof_data.get('numTotalReviews') or prof_data.get('reviewCount'),
                                'sales_last_12_months': prof_data.get('salesLast12Months'),
                                'total_sales': prof_data.get('totalSales'),
                                'bio': clean_text(prof_data.get('bio', '')),
                                'location': prof_data.get('location', ''),
                            })
                            if profile['name']:
                                break
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
            
            # Fallback: Parse HTML
            if not profile['name']:
                # Try multiple name selectors
                name_selectors = ['h1', '[data-test="agent-name"]', '.agent-name', '.profile-name']
                for sel in name_selectors:
                    name_elem = soup.select_one(sel)
                    if name_elem:
                        name_text = clean_text(name_elem.get_text())
                        if name_text and len(name_text) > 1:
                            profile['name'] = name_text
                            break
            
            # Parse rating (look for "5.0" pattern)
            if not profile['rating']:
                # Look for rating element
                rating_elem = soup.select_one('[data-test="rating"], .rating, [class*="rating"]')
                if rating_elem:
                    rating_text = rating_elem.get_text()
                    rating_match = re.search(r'(\d+\.?\d*)', rating_text)
                    if rating_match:
                        try:
                            profile['rating'] = float(rating_match.group(1))
                        except ValueError:
                            pass
                
                # Also try to find rating in text like "5.0 ★ 1,594 team reviews"
                body_text = soup.get_text()
                rating_pattern = re.search(r'(\d\.\d)\s*[★⭐]?\s*[\d,]+\s*(?:team\s+)?reviews?', body_text, re.I)
                if rating_pattern:
                    try:
                        profile['rating'] = float(rating_pattern.group(1))
                    except ValueError:
                        pass
                        
            # Extract Brokerage if missing
            if not profile['brokerage']:
                # Method 1: Look for "Real Estate Agent in [City] [Brokerage]" type titles/meta
                title = soup.title.string if soup.title else ""
                # "Tami Pardee - Real Estate Agent in Venice, CA - Reviews | Zillow" - doesn't have it
                
                # Method 2: Look for "Founder and CEO of [Brokerage]" or similar in bio text
                # "Founder and CEO of Pardee Properties"
                bio_text = profile['bio'] or soup.get_text()
                brokerage_patterns = [
                    r'of\s+([A-Z][a-zA-Z0-9\s&]{2,30})\s*,', # of Pardee Properties,
                    r'at\s+([A-Z][a-zA-Z0-9\s&]{2,30})',      # at Re/Max
                    r'Brokered by\s+([A-Z][a-zA-Z0-9\s&]{2,30})'
                ]
                for pattern in brokerage_patterns:
                    match = re.search(pattern, bio_text)
                    if match:
                        candidate = match.group(1).strip()
                        # Sanity check length and content
                        if len(candidate) > 3 and len(candidate) < 50 and 'Zillow' not in candidate:
                            profile['brokerage'] = candidate
                            break
                            
                # Method 3: Meta description fallback
                if not profile['brokerage']:
                    desc = soup.find('meta', {'name': 'description'})
                    if desc:
                        # "Find great Venice, CA real estate professionals on Zillow like Tami Pardee of Pardee Properties"
                        desc_text = desc.get('content', '')
                        match = re.search(r'like\s+.*?of\s+([A-Z][a-zA-Z0-9\s&]+)', desc_text)
                        if match:
                            profile['brokerage'] = match.group(1).strip()
            
            # Parse reviews count
            if not profile['reviews_count']:
                reviews_pattern = re.search(r'([\d,]+)\s*(?:team\s+)?reviews?', soup.get_text(), re.I)
                if reviews_pattern:
                    try:
                        profile['reviews_count'] = int(reviews_pattern.group(1).replace(',', ''))
                    except ValueError:
                        pass
            
            # Parse sales data
            body_text = soup.get_text()
            
            # Sales last 12 months
            if not profile['sales_last_12_months']:
                sales_12m_pattern = re.search(r'([\d,]+)\s*(?:team\s+)?sales?\s+last\s+12\s+months', body_text, re.I)
                if sales_12m_pattern:
                    try:
                        profile['sales_last_12_months'] = int(sales_12m_pattern.group(1).replace(',', ''))
                        profile['sales_count'] = profile['sales_last_12_months']
                    except ValueError:
                        pass
            
            # Total sales
            if not profile['total_sales']:
                total_sales_pattern = re.search(r'([\d,]+)\s*(?:total\s+)?sales\s+in', body_text, re.I)
                if total_sales_pattern:
                    try:
                        profile['total_sales'] = int(total_sales_pattern.group(1).replace(',', ''))
                    except ValueError:
                        pass
            
            # Price range
            price_range_pattern = re.search(r'\$[\d.]+[KM]?\s*-\s*\$[\d.]+[KM]?\s*(?:team\s+)?price\s+range', body_text, re.I)
            if price_range_pattern:
                # Use regex sub to be case insensitive for removal
                profile['price_range'] = re.sub(r'\s*(?:team\s+)?price\s+range', '', price_range_pattern.group(0), flags=re.IGNORECASE).strip()
            
            # Location from breadcrumb
            if not profile['location']:
                breadcrumb = soup.select_one('[class*="breadcrumb"]')
                if breadcrumb:
                    profile['location'] = clean_text(breadcrumb.get_text())
            
            if not profile['name']:
                raise NotFoundException(f"Agent not found: {profile_url}")
            
            return {
                'source_url': profile_url,
                'result': profile
            }
            
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to get agent info: {e}")
            raise ScraperException(f"Failed to get agent info: {e}")
    
    def get_agent_reviews(self, agentname: str = None, url: str = None, page: int = 1) -> Dict[str, Any]:
        """
        Get agent reviews.
        
        Args:
            agentname: Agent screen name
            url: Direct profile URL
            page: Page number
            
        Returns:
            Dict with 'results', 'total_reviews', and 'current_page'
        """
        if url:
            profile_url = url.rstrip('/') + '/reviews/'
        elif agentname:
            profile_url = f"{self.BASE_URL}/profile/{agentname}/reviews/"
        else:
            raise ValueError("Either agentname or url must be provided")
            
        if page > 1:
            profile_url = f"{profile_url}?page={page}"
        
        try:
            soup = self.get_soup(profile_url)
            reviews = []
            
            # Try script data
            script_data = extract_json_from_script(soup)
            total_reviews = 0
            
            if script_data:
                reviews_data = script_data.get('reviews', []) or script_data.get('reviewsList', [])
                
                # Check for reviewsData (common in newer Next.js pages)
                if not reviews_data and 'reviewsData' in script_data:
                    reviews_data_obj = script_data['reviewsData']
                    reviews_data = reviews_data_obj.get('reviews', [])
                    # Get total count from reviewsData
                    total_reviews = reviews_data_obj.get('totalCount') or reviews_data_obj.get('reviewCount') or 0
                
                # Try to get total from top-level (found via inspection)
                if not total_reviews:
                    # Try displayUser.ratings.count
                    display_user = script_data.get('displayUser', {})
                    if display_user:
                        ratings = display_user.get('ratings', {})
                        total_reviews = ratings.get('count', 0)
                    
                    # Fallback to graphQLData path
                    if not total_reviews:
                        graphql_data = script_data.get('graphQLData', {})
                        professional = graphql_data.get('professional', {})
                        review_ratings = professional.get('reviewRatings', {})
                        total_reviews = review_ratings.get('count', 0)
                    
                for review_data in reviews_data:
                    parsed = parse_review(review_data)
                    if parsed:
                        reviews.append(parsed)
            
            # Fallback: Parse HTML
            if not reviews:
                review_elements = soup.select('[data-test="review-card"], .review-card')
                for elem in review_elements:
                    rating_elem = elem.select_one('[data-test="rating"], .rating')
                    text_elem = elem.select_one('[data-test="review-text"], .review-text')
                    
                    rating = 0
                    if rating_elem:
                        rating_text = rating_elem.get_text()
                        try:
                            rating = int(re.search(r'\d+', rating_text).group())
                        except (AttributeError, ValueError):
                            pass
                    
                    reviews.append({
                        'zuid': '',
                        'rating': rating,
                        'review': clean_text(text_elem.get_text()) if text_elem else '',
                    })
            
            if not reviews:
                raise NotFoundException(f"No reviews found for agent: {profile_url}")
            
            return {
                'source_url': profile_url,
                'total_reviews': total_reviews,
                'results': reviews,
                'current_page': page
            }
            
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to get agent reviews: {e}")
            raise ScraperException(f"Failed to get agent reviews: {e}")
    
    def _extract_zuid(self, script_data: Dict, soup: Any) -> Optional[str]:
        """Extract encodedZuid from script data or soup."""
        # Try JSON path
        if script_data:
            props = script_data.get('props', {}).get('pageProps', {})
            agent = props.get('agent', {}) or props.get('agentDirectoryFinderDisplay', {}).get('agent', {})
            zuid = agent.get('encodedZuid') or agent.get('zuid')
            if zuid:
                return zuid
        
        # Fallback regex
        if soup:
            text = str(soup)
            match = re.search(r'"encodedZuid":"([^"]+)"', text) or re.search(r'"zuid":"([^"]+)"', text)
            if match:
                return match.group(1)
        return None

    def _fetch_agent_listings_api(self, zuid: str, property_type: str, page: int = 1) -> Dict[str, Any]:
        """Fetch agent listings from internal API."""
        endpoint_map = {
            'for-sale': 'active-listings',
            'for-rent': 'rental-listings',
            'sold': 'past-sales'
        }
        endpoint = endpoint_map.get(property_type, 'active-listings')
        
        # API URL
        api_url = f"https://www.zillow.com/profile-page/api/public/v1/{endpoint}"
        params = {
            'encodedZuid': zuid,
            'page': page,
            'include_team': 'true',
            'perPage': 40,
            'limit': 40
        }
        
        try:
            # Add delay to avoid rate limits
            # self._delay()
            
            logger.info(f"Fetching properties from API: {api_url} with params {params}")
            # Use _make_request from BaseScraper
            response = self._make_request(api_url, params=params)
                
            if response.status_code == 200:
                data = response.json()
                return data
            logger.warning(f"API request failed: {response.status_code} {response.text}")
        except Exception as e:
            logger.error(f"API request exception: {e}")
            
        return {}


    def _get_agent_slug(self, agentname: str = None, url: str = None) -> str:
        """Extract agent slug from agentname or URL for cache key."""
        if agentname:
            return agentname.lower().strip()
        if url:
            # Extract slug from URL like /profile/Pardee-Properties/
            match = re.search(r'/profile/([^/]+)', url)
            if match:
                return match.group(1).lower().strip()
        return ''
    
    def _get_cached_zuid(self, agent_slug: str) -> Optional[str]:
        """Check Redis cache for a previously stored ZUID."""
        if not agent_slug:
            return None
        cache_key = f"zuid:{agent_slug}"
        zuid = cache.get(cache_key)
        if zuid:
            logger.info(f"ZUID cache HIT for '{agent_slug}': {zuid}")
        return zuid
    
    def _cache_zuid(self, agent_slug: str, zuid: str):
        """Store a ZUID in Redis cache for future use."""
        if agent_slug and zuid:
            cache_key = f"zuid:{agent_slug}"
            cache.set(cache_key, zuid, ZUID_CACHE_TIMEOUT)
            logger.info(f"Cached ZUID for '{agent_slug}': {zuid}")

    def get_agent_properties(
        self,
        agentname: str = None,
        url: str = None,
        property_type: str = 'for-sale',
        page: int = 1,
    ) -> Dict[str, Any]:
        """
        Get agent's properties.
        
        Optimized flow:
        1. Check Redis for cached ZUID → if found, skip HTML download (~1s)
        2. If no cached ZUID, download profile HTML to extract it (~8s)
        3. Call Zillow internal API with ZUID to get listings
        
        Args:
            agentname: Agent screen name
            url: Direct profile URL
            property_type: 'for-sale', 'for-rent', or 'sold'
            page: Page number
            
        Returns:
            Dict with 'results' and metadata
        """
        if url:
            # Strip any /listings/... path from the URL to ensure we hit the main profile
            profile_url = re.sub(r'/listings/.*$', '', url.rstrip('/')) + '/'
        elif agentname:
            profile_url = f"{self.BASE_URL}/profile/{agentname}/"
        else:
            raise ValueError("Either agentname or url must be provided")
        
        agent_slug = self._get_agent_slug(agentname, url)
        
        try:
            properties = []
            total_properties = 0
            listings = []
            soup = None
            script_data = None
            
            # === FAST PATH: Check ZUID cache first ===
            zuid = self._get_cached_zuid(agent_slug)
            
            if zuid:
                # Skip the expensive HTML download entirely!
                logger.info(f"Using cached ZUID: {zuid}, skipping HTML download")
                api_data = self._fetch_agent_listings_api(zuid, property_type, page)
                if api_data:
                    api_listings = api_data.get('listings') or api_data.get('past_sales') or []
                    api_total = api_data.get('listing_count') or api_data.get('total') or 0
                    
                    if api_listings:
                        listings = api_listings
                        total_properties = api_total or len(listings)
                        logger.info(f"API fetch successful (cached ZUID): {len(listings)} listings, {total_properties} total")
            
            # === SLOW PATH: Download HTML to extract ZUID ===
            if not listings:
                soup = self.get_soup(profile_url)
                script_data = extract_json_from_script(soup)
                
                zuid = self._extract_zuid(script_data, soup)
                if zuid:
                    # Cache the ZUID for future requests
                    self._cache_zuid(agent_slug, zuid)
                    
                    logger.info(f"Found ZUID: {zuid}, attempting internal API fetch")
                    api_data = self._fetch_agent_listings_api(zuid, property_type, page)
                    if api_data:
                        api_listings = api_data.get('listings') or api_data.get('past_sales') or []
                        api_total = api_data.get('listing_count') or api_data.get('total') or 0
                        
                        if api_listings:
                            listings = api_listings
                            total_properties = api_total or len(listings)
                            logger.info(f"API fetch successful: {len(listings)} listings, {total_properties} total")

            # 2. Fallback to Script Data (Next.js props) if API failed
            if not listings and script_data:
                # Map property type to Next.js prop keys
                prop_keys = {
                    'for-sale': ['forSaleListings', 'listings', 'properties'],
                    'for-rent': ['forRentListings', 'listings', 'properties'],
                    'sold': ['pastSales', 'soldListings', 'listings'],
                }
                
                target_keys = prop_keys.get(property_type, ['listings'])
                
                for key in target_keys:
                    found = script_data.get(key)
                    if found:
                        logger.info(f"Found property data under key '{key}'")
                        if isinstance(found, dict):
                            logger.info(f"Keys in '{key}': {list(found.keys())}")
                            listings = found.get('listings') or found.get('past_sales') or []
                            total_properties = found.get('totalCount') or found.get('totalResultCount') or found.get('count') or 0
                            logger.info(f"Extracted total_properties: {total_properties}")
                        elif isinstance(found, list):
                            listings = found
                            total_properties = len(found)
                        
                        if listings:
                            break
            
            # Fallback: Parse total count from HTML text (e.g., "Sold (3254)")
            if (total_properties == 0 or total_properties == len(listings)) and soup:
                 # Look for headers like "Sold (3254)", "Rentals (13)", "For Sale (10)"
                 labels = []
                 if property_type == 'sold':
                     labels = ['Sold', 'Past Sales']
                 elif property_type == 'for-rent':
                     labels = ['For Rent', 'Rentals', 'Active Rentals']
                 else:
                     labels = ['For Sale', 'Active Listings', 'Listings']
                 
                 # Create combined regex: (Sold|Past Sales)\s*\((\d+)\)
                 label_pattern = '|'.join(map(re.escape, labels))
                 count_pattern = re.compile(rf'({label_pattern})\s*\((\d+)\)', re.IGNORECASE)
                 
                 for elem in soup.find_all(['h2', 'h3', 'span', 'div', 'button']):
                     text = elem.get_text().strip()
                     match = count_pattern.search(text)
                     if match:
                         total_properties = int(match.group(2))
                         logger.info(f"Extracted total_properties from HTML: {total_properties} (matched '{match.group(1)}')")
                         break
                         
            for listing in listings:
                parsed = parse_property_card(listing)
                if parsed:
                    properties.append(parsed)
            
            # Fallback: Parse HTML if no properties found (and no JSON listings)
            if not properties and not listings and soup:
                property_cards = soup.select('[data-test="property-card"], .property-card, .list-card')
                for card in property_cards:
                    # Extract basic info
                    price_elem = card.select_one('[data-test="property-card-price"], .list-card-price')
                    address_elem = card.select_one('[data-test="property-card-addr"], .list-card-addr')
                    link_elem = card.select_one('a[href*="/homedetails/"]')
                    
                    if address_elem:
                        properties.append({
                            'zpid': None,
                            'address': clean_text(address_elem.get_text()),
                            'price': clean_text(price_elem.get_text()) if price_elem else None,
                            'url': f"{self.BASE_URL}{link_elem['href']}" if link_elem else None,
                            'property_type': property_type,
                            'status': None
                        })
            
            if not properties:
                raise NotFoundException(f"No {property_type} properties found for agent")
            
            if total_properties == 0 and properties:
                total_properties = len(properties)
                
            # Determine per_page based on listings count if explicit
            per_page = 40
            if zuid and listings:
                 current_count = len(listings)
                 # If using API, the returned count is likely the page size (unless it's the last page)
                 # Heuristic: If we have more total results than what would cover the current page count
                 if current_count > 0 and total_properties > (page * current_count):
                      per_page = current_count
                 elif current_count > 0 and page == 1:
                      per_page = current_count # First page defines page size regardless of total

            return {
                'source_url': profile_url,
                'results': properties,
                'total_results': total_properties,
                'current_page': page,
                'per_page': per_page
            }
            
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to get agent properties: {e}")
            raise ScraperException(f"Failed to get agent properties: {e}")


# Singleton instance
agent_scraper = AgentScraper()
