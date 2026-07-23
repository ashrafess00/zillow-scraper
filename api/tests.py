"""
Tests for the Zillow scraper API.
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase
from rest_framework import status

from api.serializers import AgentSerializer, PropertySerializer, ReviewSerializer
from core.proxy_manager import ProxyManager
from core.user_agent_manager import UserAgentManager
from scrapers.base import NotFoundException


class SerializerTests(TestCase):
    """Tests for API serializers."""
    
    def test_agent_serializer_valid(self):
        """Test AgentSerializer with valid data."""
        data = {
            'name': 'John Doe',
            'url': 'https://www.zillow.com/profile/johndoe',
            'location': 'Los Angeles, CA',
        }
        serializer = AgentSerializer(data=data)
        self.assertTrue(serializer.is_valid())
    
    def test_agent_serializer_invalid(self):
        """Test AgentSerializer with invalid data."""
        data = {'location': 'Los Angeles'}  # Missing required fields
        serializer = AgentSerializer(data=data)
        self.assertFalse(serializer.is_valid())
    
    def test_property_serializer_valid(self):
        """Test PropertySerializer with valid data."""
        data = {
            'zpid': 123456,
            'address': '123 Main St',
            'url': 'https://www.zillow.com/homedetails/123_zpid',
            'price': 500000.0,
            'beds': 3,
            'baths': 2,
            'sqft': 1500,
        }
        serializer = PropertySerializer(data=data)
        self.assertTrue(serializer.is_valid())
    
    def test_review_serializer_valid(self):
        """Test ReviewSerializer with valid data."""
        data = {
            'zuid': 'user123',
            'rating': 5,
            'review': 'Great agent!',
        }
        serializer = ReviewSerializer(data=data)
        self.assertTrue(serializer.is_valid())


class ProxyManagerTests(TestCase):
    """Tests for the proxy manager."""
    
    @patch('core.proxy_manager.settings')
    def test_no_proxies_returns_none(self, mock_settings):
        """Test that get_proxy returns None when no proxies configured."""
        mock_settings.SCRAPER_SETTINGS = {'PROXIES': []}
        manager = ProxyManager()
        self.assertIsNone(manager.get_proxy())
    
    @patch('core.proxy_manager.settings')
    def test_proxy_rotation(self, mock_settings):
        """Test proxy configuration."""
        mock_settings.SCRAPER_SETTINGS = {
            'PROXIES': ['http://proxy1:8080', 'http://proxy2:8080']
        }
        
        manager = ProxyManager()
        proxy = manager.get_proxy()
        
        self.assertIsNotNone(proxy)
        self.assertEqual(proxy['http'], 'http://proxy1:8080')
        self.assertEqual(proxy['https'], 'http://proxy1:8080')


class UserAgentManagerTests(TestCase):
    """Tests for the user-agent manager."""
    
    def test_get_random_user_agent(self):
        """Test that get_random_user_agent returns a string."""
        manager = UserAgentManager()
        ua = manager.get_random_user_agent()
        
        self.assertIsInstance(ua, str)
        self.assertGreater(len(ua), 0)
    
    def test_get_chrome_user_agent(self):
        """Test Chrome user agent contains Chrome."""
        manager = UserAgentManager()
        ua = manager.get_chrome_user_agent()
        
        self.assertIn('Chrome', ua)


class APIEndpointTests(APITestCase):
    """Integration tests for API endpoints."""
    
    @patch('api.views.agent_scraper')
    def test_agent_by_location(self, mock_scraper):
        """Test agentByLocation endpoint."""
        mock_scraper.get_agents_by_location.return_value = {
            'results': [
                {'name': 'Test Agent', 'url': 'http://test.com', 'location': 'LA'}
            ],
            'total_results': 1,
            'current_page': 1
        }
        response = self.client.get('/agentByLocation', {'location': 'los-angeles'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['name'], 'Test Agent')
    
    @patch('api.views.property_scraper')
    def test_by_location(self, mock_scraper):
        """Test bylocation endpoint."""
        mock_scraper.search_by_location.return_value = {
            'results': [
                {
                    'zpid': 123,
                    'address': '123 Test St',
                    'url': 'http://test.com',
                    'price': 500000,
                    'beds': 3,
                    'baths': 2,
                    'sqft': 1500,
                }
            ],
            'total_results': 1,
            'current_page': 1
        }
        
        response = self.client.get('/bylocation', {'location': 'seattle-wa'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
    
    @patch('api.views.property_scraper')
    def test_autocomplete(self, mock_scraper):
        """Test autocomplete endpoint."""
        mock_scraper.autocomplete.return_value = [
            {'display': 'Los Angeles, CA', 'type': 'city', 'id': '123'}
        ]
        
        response = self.client.get('/autocomplete', {'q': 'los'})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
    
    def test_autocomplete_missing_query(self):
        """Test autocomplete endpoint without query."""
        response = self.client.get('/autocomplete')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)
    
    def test_by_coordinates_missing_params(self):
        """Test bycoordinates endpoint without required params."""
        response = self.client.get('/bycoordinates')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)
    
    def test_agent_info_missing_params(self):
        """Test agentInfo endpoint without required params."""
        response = self.client.get('/agentInfo')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)


class HealthCheckTests(APITestCase):
    """Tests for the /health endpoint."""
    
    def test_health_ok(self):
        """Test health endpoint reports component status."""
        response = self.client.get('/health')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(response.data['status'], ('ok', 'degraded'))
        self.assertIn('database', response.data['checks'])
        self.assertIn('cache', response.data['checks'])


@override_settings(RAPIDAPI_PROXY_SECRET='s3cret')
class RapidAPIOnlyMiddlewareTests(APITestCase):
    """Tests for the RapidAPI-only gate."""
    
    def test_request_without_secret_is_rejected(self):
        """Test a direct call with no proxy secret is refused."""
        response = self.client.get('/autocomplete', {'q': 'los'})
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_request_with_wrong_secret_is_rejected(self):
        """Test a call with the wrong proxy secret is refused."""
        response = self.client.get('/autocomplete', {'q': 'los'}, HTTP_X_RAPIDAPI_PROXY_SECRET='nope')
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_request_with_secret_passes_through(self):
        """Test a RapidAPI-proxied call reaches the view."""
        with patch('scrapers.property_scraper.property_scraper.autocomplete', return_value=[]):
            response = self.client.get(
                '/autocomplete', {'q': 'los'}, HTTP_X_RAPIDAPI_PROXY_SECRET='s3cret'
            )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_health_is_exempt(self):
        """Test the health probe works without the proxy secret."""
        response = self.client.get('/health')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    
    def test_unset_secret_fails_open(self):
        """Test enforcement is off when no secret is configured."""
        with override_settings(RAPIDAPI_PROXY_SECRET=''):
            with patch('scrapers.property_scraper.property_scraper.autocomplete', return_value=[]):
                response = self.client.get('/autocomplete', {'q': 'los'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)


LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def _load_property_fixture():
    """Load the synthetic homedetails property object used by the detail tests."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent / 'fixtures' / 'property_homedetails.json'
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _homedetails_soup(property_obj):
    """Build a BeautifulSoup homedetails page embedding property_obj in gdpClientCache."""
    import json
    from bs4 import BeautifulSoup
    gdp = json.dumps({'Property': {'property': property_obj}})
    next_data = json.dumps({'props': {'pageProps': {'componentProps': {'gdpClientCache': gdp}}}})
    html = (
        '<html><head><title>123 Main St | Zillow</title>'
        '<script id="__NEXT_DATA__" type="application/json">' + next_data + '</script>'
        '</head><body></body></html>'
    )
    return BeautifulSoup(html, 'html.parser')


@override_settings(CACHES=LOCMEM_CACHE)
class PropertyDetailScraperTests(TestCase):
    """Tests for the zpid-based property detail parsers."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.fixture = _load_property_fixture()

    def _scraper(self):
        from scrapers.property_scraper import property_scraper
        return property_scraper

    def test_get_property_data_extracts_and_caches(self):
        """One fetch warms the cache; a second call does not re-fetch."""
        scraper = self._scraper()
        soup = _homedetails_soup(self.fixture)
        with patch.object(scraper, 'get_soup', return_value=soup) as mock_soup:
            first = scraper._get_property_data(12345678)
            second = scraper._get_property_data(12345678)

        self.assertEqual(first['zpid'], 12345678)
        self.assertEqual(second['zpid'], 12345678)
        self.assertEqual(mock_soup.call_count, 1)  # second served from cache

    def test_get_property_data_not_found(self):
        """A page with no property object raises NotFoundException."""
        from scrapers.base import NotFoundException
        from bs4 import BeautifulSoup
        scraper = self._scraper()
        empty = BeautifulSoup('<html><head><title>x</title></head></html>', 'html.parser')
        with patch.object(scraper, 'get_soup', return_value=empty):
            with self.assertRaises(NotFoundException):
                scraper._get_property_data(999)

    def test_get_property_details_mapping(self):
        from api.serializers import PropertyDetailsSerializer
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture):
            d = scraper.get_property_details(12345678)
        self.assertEqual(d['zpid'], 12345678)
        self.assertEqual(d['address'], '123 Main St, Austin, TX, 78701')
        self.assertEqual(d['price'], 750000)
        self.assertEqual(d['zestimate'], 762300)
        self.assertEqual(d['beds'], 4)
        self.assertEqual(d['price_per_sqft'], 300.0)
        self.assertEqual(d['photo_count'], 2)
        self.assertEqual(d['photo_url'], 'https://photos.zillowstatic.com/large_1.jpg')
        self.assertEqual(d['brokerage'], 'Acme Realty')
        self.assertEqual(d['hoa_fee'], 50)  # parsed from the string "50 monthly"
        # The parser output must serialize cleanly (guards against type mismatches).
        self.assertEqual(PropertyDetailsSerializer(d).data['hoa_fee'], 50.0)

    def test_get_zestimate(self):
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture):
            z = scraper.get_zestimate(12345678)
        self.assertEqual(z['zestimate'], 762300)
        self.assertEqual(z['rent_zestimate'], 3400)

    def test_get_price_history(self):
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture):
            events = scraper.get_price_history(12345678)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event'], 'Listed for sale')
        self.assertEqual(events[0]['price'], 750000)

    def test_get_tax_history_year_from_epoch(self):
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture):
            events = scraper.get_tax_history(12345678)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['year'], 2023)
        self.assertEqual(events[0]['tax_paid'], 9500)

    def test_get_property_photos(self):
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture):
            photos = scraper.get_property_photos(12345678)
        self.assertEqual(photos, [
            'https://photos.zillowstatic.com/large_1.jpg',
            'https://photos.zillowstatic.com/large_2.jpg',
        ])

    def test_get_schools(self):
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture):
            result = scraper.get_schools(12345678)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['name'], 'Austin High School')
        self.assertEqual(result[0]['rating'], 8)

    def test_get_similar_homes(self):
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture):
            homes = scraper.get_similar_homes(12345678)
        self.assertEqual(len(homes), 2)
        self.assertEqual(homes[0]['zpid'], 22222222)
        self.assertEqual(homes[0]['address'], '456 Oak Ave, Austin, TX, 78701')
        self.assertEqual(homes[0]['photo_url'], 'https://photos.zillowstatic.com/nearby_1.jpg')


@override_settings(CACHES=LOCMEM_CACHE)
class PropertyDetailEndpointTests(APITestCase):
    """Tests for the zpid-based property detail endpoints (routing + validation)."""

    def setUp(self):
        # cache_page() writes to the default cache, which Django does NOT isolate
        # between tests/runs. Use an in-process cache and clear it so a cached
        # response never masks a later call.
        from django.core.cache import cache
        cache.clear()

    def test_property_requires_zpid(self):
        response = self.client.get('/property')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)

    def test_property_rejects_non_integer_zpid(self):
        response = self.client.get('/property', {'zpid': 'abc'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)

    @patch('api.views.property_scraper')
    def test_property_detail(self, mock_scraper):
        mock_scraper.get_property_details.return_value = {
            'zpid': 12345678, 'address': '123 Main St', 'price': 750000.0,
        }
        response = self.client.get('/property', {'zpid': '12345678'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['zpid'], 12345678)
        mock_scraper.get_property_details.assert_called_once_with(12345678)

    @patch('api.views.property_scraper')
    def test_photos_endpoint(self, mock_scraper):
        mock_scraper.get_property_photos.return_value = ['a.jpg', 'b.jpg']
        response = self.client.get('/photos', {'zpid': '12345678'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(response.data['photos'], ['a.jpg', 'b.jpg'])

    @patch('api.views.property_scraper')
    def test_similar_homes_endpoint(self, mock_scraper):
        mock_scraper.get_similar_homes.return_value = [
            {'zpid': 22222222, 'address': '456 Oak Ave', 'price': 720000.0},
        ]
        response = self.client.get('/similarHomes', {'zpid': '12345678'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['zpid'], 22222222)


class SearchStatusSortTests(TestCase):
    """Tests for listing-type toggles and sort in the search query state."""

    def _scraper(self):
        from scrapers.property_scraper import property_scraper
        return property_scraper

    def test_resolve_sort_friendly_and_passthrough(self):
        from scrapers.property_scraper import resolve_sort
        self.assertIsNone(resolve_sort(None))
        self.assertIsNone(resolve_sort(''))
        self.assertEqual(resolve_sort('newest'), 'days')
        self.assertEqual(resolve_sort('price_high'), 'priced')
        self.assertEqual(resolve_sort('PRICE_LOW'), 'pricea')
        # Unknown tokens pass through unchanged (Zillow ignores bad ones).
        self.assertEqual(resolve_sort('globalrelevanceex'), 'globalrelevanceex')

    def test_query_state_for_sale_is_default(self):
        fs = self._scraper()._build_search_query_state(list_type='for-sale')
        self.assertNotIn('isForRent', fs)
        self.assertNotIn('isRecentlySold', fs)

    def test_query_state_for_rent_toggles(self):
        fs = self._scraper()._build_search_query_state(list_type='for-rent')
        self.assertEqual(fs['isForRent'], {'value': True})
        self.assertEqual(fs['isRecentlySold'], {'value': False})
        self.assertEqual(fs['isForSaleByAgent'], {'value': False})

    def test_query_state_sold_toggles(self):
        fs = self._scraper()._build_search_query_state(list_type='sold')
        self.assertEqual(fs['isRecentlySold'], {'value': True})
        self.assertEqual(fs['isForRent'], {'value': False})

    def test_query_state_includes_sort(self):
        fs = self._scraper()._build_search_query_state(list_type='for-sale', sort='newest')
        self.assertEqual(fs['sortSelection'], {'value': 'days'})

    def test_build_search_url_appends_sort(self):
        from scrapers.utils import build_search_url
        plain = build_search_url('austin-tx', 'for-sale', 1)
        self.assertEqual(plain, 'https://www.zillow.com/austin-tx/')
        sorted_url = build_search_url('austin-tx', 'for-sale', 1, sort='days')
        self.assertIn('/austin-tx/?', sorted_url)
        self.assertIn('sortSelection', sorted_url)

    def test_map_bounds_passes_list_type_into_query_state(self):
        """Regression: coordinates/mapbounds/polygon must honor listType (was ignored)."""
        import json
        from urllib.parse import parse_qs, urlparse
        scraper = self._scraper()
        captured = {}

        def fake_get_soup(url):
            captured['url'] = url
            raise NotFoundException("stop here")  # we only care about the built URL

        with patch.object(scraper, 'get_soup', side_effect=fake_get_soup):
            with self.assertRaises(NotFoundException):
                scraper.search_by_map_bounds(
                    north=30.3, south=30.2, east=-97.7, west=-97.8,
                    list_type='for-rent', sort='newest',
                )
        qs = parse_qs(urlparse(captured['url']).query)
        state = json.loads(qs['searchQueryState'][0])
        self.assertEqual(state['filterState']['isForRent'], {'value': True})
        self.assertEqual(state['filterState']['sortSelection'], {'value': 'days'})


class ByAddressTests(APITestCase):
    """Tests for the /byAddress endpoint."""

    def test_requires_address(self):
        response = self.client.get('/byAddress')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)

    def test_address_resolves_to_details(self):
        """A matched zpid is looked up for full details."""
        from scrapers.property_scraper import property_scraper
        with patch.object(property_scraper, 'search_by_location',
                          return_value={'results': [{'zpid': 12345678}], 'total_results': 1}) as m_loc, \
             patch.object(property_scraper, 'get_property_details',
                          return_value={'zpid': 12345678, 'address': '123 Main St', 'price': 750000.0}) as m_det:
            response = self.client.get('/byAddress', {'address': '123 Main St, Austin, TX'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['zpid'], 12345678)
        m_loc.assert_called_once()
        m_det.assert_called_once_with(12345678)

    def test_address_not_found(self):
        """No match returns the empty-result contract (HTTP 200)."""
        from scrapers.property_scraper import property_scraper
        with patch.object(property_scraper, 'search_by_location',
                          side_effect=NotFoundException('nope')):
            response = self.client.get('/byAddress', {'address': 'nowhere'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
