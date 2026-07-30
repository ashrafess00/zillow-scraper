"""
Tests for the Zillow scraper API.
"""

import json
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
        """Test Chrome user agent identifies as Chrome.

        fake-useragent's `.chrome` pool includes mobile builds, which spell the
        token 'CriOS' (iOS) rather than 'Chrome' — asserting on 'Chrome' alone
        made this test fail roughly one run in ten.
        """
        manager = UserAgentManager()
        ua = manager.get_chrome_user_agent()

        self.assertTrue(
            'Chrome' in ua or 'CriOS' in ua,
            f"expected a Chrome user agent, got: {ua}",
        )


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

    @patch('api.views.property_scraper')
    def test_autocomplete_exposes_region_and_zpid(self, mock_scraper):
        """Region hits surface region_id; address hits surface zpid."""
        mock_scraper.autocomplete.return_value = [
            {'display': 'Seattle, WA', 'type': 'region', 'id': '16037',
             'region_id': 16037, 'region_type': 'city', 'latitude': 47.6,
             'longitude': -122.3},
            {'display': '1006 Hollybluff St Austin, TX 78753', 'type': 'address',
             'id': '', 'zpid': 29429121, 'address_type': 'forsale_address'},
        ]
        response = self.client.get('/autocomplete', {'q': 'seattle'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]['region_id'], 16037)
        self.assertEqual(response.data[0]['region_type'], 'city')
        self.assertEqual(response.data[1]['zpid'], 29429121)

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

    def test_health_reports_enforcement_state(self):
        """/health answers 'is the header arriving?' without log archaeology."""
        response = self.client.get('/health')
        rapidapi = response.data['rapidapi']

        self.assertTrue(rapidapi['enforcing'])
        self.assertIn('requests_with_header', rapidapi)
        self.assertIn('requests_without_header', rapidapi)

    def test_health_counts_header_bearing_requests_when_failing_open(self):
        from core.middleware import RapidAPIOnlyMiddleware

        before = RapidAPIOnlyMiddleware.seen_with_header
        with override_settings(RAPIDAPI_PROXY_SECRET=''):
            with patch('scrapers.property_scraper.property_scraper.autocomplete', return_value=[]):
                self.client.get(
                    '/autocomplete', {'q': 'los'}, HTTP_X_RAPIDAPI_PROXY_SECRET='anything'
                )
            response = self.client.get('/health')

        self.assertEqual(RapidAPIOnlyMiddleware.seen_with_header, before + 1)
        self.assertFalse(response.data['rapidapi']['enforcing'])
        self.assertIn('advice', response.data['rapidapi'])


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


@override_settings(CACHES=LOCMEM_CACHE)
class SimilarHomesTests(TestCase):
    """
    Tests for the rebuilt /similarHomes comp search.

    Zillow does not ship `nearbyHomes` on the property object (verified live
    2026-07-30) and its comps API is behind a persisted-query safelist, so comps
    are rebuilt from a map-bounds search around the subject. These tests mock
    `_search_around` — the ranking, not the fetch, is what's under test.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.fixture = _load_property_fixture()

    def _scraper(self):
        from scrapers.property_scraper import property_scraper
        return property_scraper

    @staticmethod
    def _card(zpid, **overrides):
        """A search card matching the fixture subject, before overrides."""
        card = {
            'zpid': zpid, 'address': f'{zpid} Test St, Austin, TX, 78701',
            'url': f'https://www.zillow.com/homedetails/{zpid}_zpid/',
            'photo_url': f'https://photos.zillowstatic.com/{zpid}.jpg',
            'price': 750000.0, 'beds': 4, 'baths': 3, 'sqft': 2500,
            'property_type': 'SINGLE_FAMILY', 'status': 'FOR_SALE',
            'latitude': 30.2672, 'longitude': -97.7431, 'brokerage': 'Test Realty',
        }
        card.update(overrides)
        return card

    def test_ranks_closest_comp_first(self):
        """The identical-but-nearer home outranks the further, less similar one."""
        scraper = self._scraper()
        cards = [
            # ~1.4 mi away, 800 sqft smaller, one bed fewer.
            self._card(33333333, latitude=30.2872, sqft=1700, beds=3),
            # Same block, same size.
            self._card(22222222, latitude=30.2675),
        ]
        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, '_search_around', return_value=cards):
            homes = scraper.get_similar_homes(12345678)

        self.assertEqual([h['zpid'] for h in homes], [22222222, 33333333])
        self.assertLess(homes[0]['similarity_score'], homes[1]['similarity_score'])
        self.assertLess(homes[0]['distance_miles'], 0.1)
        self.assertGreater(homes[1]['distance_miles'], 1.0)

    def test_excludes_the_subject_property(self):
        scraper = self._scraper()
        cards = [self._card(12345678), self._card(22222222)]
        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, '_search_around', return_value=cards):
            homes = scraper.get_similar_homes(12345678)
        self.assertEqual([h['zpid'] for h in homes], [22222222])

    def test_respects_count(self):
        scraper = self._scraper()
        cards = [self._card(1000 + i, latitude=30.2672 + i / 1000) for i in range(20)]
        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, '_search_around', return_value=cards):
            homes = scraper.get_similar_homes(12345678, count=3)
        self.assertEqual(len(homes), 3)

    def test_widens_search_when_tight_box_is_thin(self):
        """A sparse tight box triggers exactly one wider retry."""
        scraper = self._scraper()
        calls = []

        def fake_search(lat, lng, delta, list_type):
            calls.append(delta)
            return [self._card(22222222)] if len(calls) == 1 else [
                self._card(22222222), self._card(33333333), self._card(44444444)
            ]

        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, '_search_around', side_effect=fake_search):
            homes = scraper.get_similar_homes(12345678, count=3)

        self.assertEqual(len(calls), 2)
        self.assertLess(calls[0], calls[1])
        self.assertEqual(len(homes), 3)

    def test_does_not_widen_when_tight_box_suffices(self):
        scraper = self._scraper()
        cards = [self._card(1000 + i) for i in range(10)]
        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, '_search_around', return_value=cards) as mock_search:
            scraper.get_similar_homes(12345678, count=8)
        self.assertEqual(mock_search.call_count, 1)

    def test_list_type_defaults_to_subject_status(self):
        """Comps for a sold home are other sold homes, not active listings."""
        scraper = self._scraper()
        sold = {**self.fixture, 'homeStatus': 'RECENTLY_SOLD'}
        with patch.object(scraper, '_get_property_data', return_value=sold), \
             patch.object(scraper, '_search_around', return_value=[]) as mock_search:
            scraper.get_similar_homes(12345678)
        self.assertEqual(mock_search.call_args[0][3], 'sold')

    def test_explicit_list_type_overrides_status(self):
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, '_search_around', return_value=[]) as mock_search:
            scraper.get_similar_homes(12345678, list_type='sold')
        self.assertEqual(mock_search.call_args[0][3], 'sold')

    def test_returns_empty_without_coordinates(self):
        """No lat/lng means no box to search — return [] rather than searching."""
        scraper = self._scraper()
        no_coords = {**self.fixture, 'latitude': None, 'longitude': None}
        with patch.object(scraper, '_get_property_data', return_value=no_coords), \
             patch.object(scraper, '_search_around') as mock_search:
            homes = scraper.get_similar_homes(12345678)
        self.assertEqual(homes, [])
        mock_search.assert_not_called()

    def test_search_failure_yields_empty_not_error(self):
        """A blocked/empty upstream search must not 500 the endpoint."""
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, 'search_by_map_bounds',
                          side_effect=NotFoundException('none')):
            homes = scraper.get_similar_homes(12345678)
        self.assertEqual(homes, [])

    def test_result_is_cached_per_zpid(self):
        scraper = self._scraper()
        cards = [self._card(22222222)]
        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, '_search_around', return_value=cards) as mock_search:
            first = scraper.get_similar_homes(12345678, count=1)
            second = scraper.get_similar_homes(12345678, count=1)
        self.assertEqual(first, second)
        self.assertEqual(mock_search.call_count, 1)

    def test_output_serializes(self):
        """The parser's output must survive SimilarHomeSerializer unchanged."""
        from api.serializers import SimilarHomeSerializer
        scraper = self._scraper()
        cards = [self._card(22222222, latitude=30.2675)]
        with patch.object(scraper, '_get_property_data', return_value=self.fixture), \
             patch.object(scraper, '_search_around', return_value=cards):
            homes = scraper.get_similar_homes(12345678)
        data = SimilarHomeSerializer(homes, many=True).data
        self.assertEqual(data[0]['zpid'], 22222222)
        self.assertIn('distance_miles', data[0])
        self.assertIn('similarity_score', data[0])

    def test_haversine_known_distance(self):
        """Sanity-check the distance maths against a known city pair (~2.4 mi)."""
        scraper = self._scraper()
        miles = scraper._haversine_miles(30.2672, -97.7431, 30.3005, -97.7522)
        self.assertAlmostEqual(miles, 2.36, delta=0.1)

    def test_haversine_handles_missing_point(self):
        scraper = self._scraper()
        self.assertIsNone(scraper._haversine_miles(30.2672, -97.7431, None, None))


def _load_market_fixture():
    """Load the captured home-values __NEXT_DATA__ used by the market tests."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent / 'fixtures' / 'market_home_values.json'
    with path.open() as fh:
        return json.load(fh)


@override_settings(CACHES=LOCMEM_CACHE)
class MarketStatsScraperTests(TestCase):
    """
    Tests for /marketStats.

    The fixture is a real Austin home-values `__NEXT_DATA__` payload (captured
    2026-07-30), trimmed to 4 points per time series. Its `parentage` array
    deliberately keeps three child zipcodes alongside the five true ancestors,
    because separating those is the parser's job.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.fixture = _load_market_fixture()
        self.page_props = self.fixture['props']['pageProps']

    def _scraper(self):
        from scrapers.property_scraper import property_scraper
        return property_scraper

    def _stats(self, history=False, page_props=None, location='austin-tx'):
        scraper = self._scraper()
        props = page_props if page_props is not None else self.page_props
        with patch.object(scraper, '_fetch_market_page',
                          return_value=(props, 'austin-tx')):
            return scraper.get_market_stats(location, history=history)

    def test_headline_figures(self):
        stats = self._stats()
        self.assertEqual(stats['home_value_index'], 507622.43)
        self.assertEqual(stats['median_list_price'], 571916.33)
        self.assertEqual(stats['median_sale_price'], 570279.17)
        self.assertEqual(stats['for_sale_inventory'], 5534.33)
        self.assertEqual(stats['new_listings'], 1434.67)
        self.assertEqual(stats['median_rent'], 1614.53)

    def test_yoy_is_converted_to_a_percentage(self):
        """Zillow ships -0.0502 as a ratio; the API publishes -5.02."""
        stats = self._stats()
        self.assertEqual(stats['home_value_index_yoy_pct'], -5.02)

    def test_days_to_pending_and_sale_to_list_come_from_the_range_arrays(self):
        """
        These never appear on the *Latest objects — only on *Range[0].

        Reading them off mrktListingLatest/mrktSaleLatest yields None, which is
        how they would silently disappear.
        """
        stats = self._stats()
        self.assertEqual(stats['median_days_to_pending'], 39.33)
        self.assertEqual(stats['median_sale_to_list_ratio'], 0.9795)
        self.assertEqual(stats['pct_sold_above_list'], 17.04)
        self.assertEqual(stats['pct_sold_below_list'], 67.22)

    def test_listing_and_sale_periods_are_reported_separately(self):
        """Zillow staggers these; collapsing them into one date would lie."""
        stats = self._stats()
        self.assertEqual(stats['listings_as_of'], '2026-06-30')
        self.assertEqual(stats['sales_as_of'], '2026-05-31')
        self.assertNotEqual(stats['listings_as_of'], stats['sales_as_of'])

    def test_region_identity(self):
        stats = self._stats()
        region = stats['region']
        self.assertEqual(region['id'], 10221)
        self.assertEqual(region['name'], 'Austin')
        self.assertEqual(region['type'], 'city')
        self.assertEqual(region['url'], 'https://www.zillow.com/austin-tx/')
        self.assertEqual(region['slug'], 'austin-tx')

    def test_parentage_excludes_child_regions(self):
        """
        Zillow's `parentage` also lists every zipcode *inside* the city — 73 of
        them for Austin. Only true ancestors should survive.
        """
        stats = self._stats()
        parentage = stats['region']['parentage']
        self.assertEqual(
            [p['type'] for p in parentage],
            ['country', 'state', 'dma', 'cbsa', 'county'],
        )
        self.assertEqual(parentage[0]['name'], 'United States')
        self.assertEqual(parentage[-1]['name'], 'Travis County')
        self.assertNotIn('78745', [p['name'] for p in parentage])

    def test_parentage_keeps_ancestors_of_a_neighborhood(self):
        """A zipcode ranks above a neighborhood, so it must be kept there."""
        props = json.loads(json.dumps(self.page_props))
        props['requestedRegion']['regionTypeName'] = 'neighborhood'
        props['zhviRegion']['regionTypeName'] = 'neighborhood'
        stats = self._stats(page_props=props)
        types = [p['type'] for p in stats['region']['parentage']]
        self.assertIn('zipcode', types)
        self.assertIn('county', types)

    def test_benchmarks(self):
        stats = self._stats()
        self.assertEqual(stats['benchmarks']['county_home_value_index'], 476400.03)
        self.assertEqual(stats['benchmarks']['state_home_value_index'], 302999.49)
        self.assertEqual(stats['benchmarks']['national_home_value_index'], 372995.19)
        self.assertEqual(stats['benchmarks']['national_median_rent'], 1965.0)

    def test_history_omitted_by_default(self):
        self.assertNotIn('history', self._stats())

    def test_history_included_on_request(self):
        stats = self._stats(history=True)
        history = stats['history']
        self.assertEqual(
            history['home_value_index'][0],
            {'date': '2026-06-30', 'value': 507622.43},
        )
        self.assertTrue(history['median_rent'])
        self.assertTrue(history['median_days_to_pending'])
        self.assertTrue(history['median_sale_to_list_ratio'])

    def test_history_is_cached_separately_from_the_summary(self):
        """A cached summary must not satisfy a history=true request."""
        scraper = self._scraper()
        with patch.object(scraper, '_fetch_market_page',
                          return_value=(self.page_props, 'austin-tx')):
            summary = scraper.get_market_stats('austin-tx')
            detailed = scraper.get_market_stats('austin-tx', history=True)
        self.assertNotIn('history', summary)
        self.assertIn('history', detailed)

    def test_result_is_cached(self):
        scraper = self._scraper()
        with patch.object(scraper, '_fetch_market_page',
                          return_value=(self.page_props, 'austin-tx')) as mock_fetch:
            first = scraper.get_market_stats('austin-tx')
            second = scraper.get_market_stats('austin-tx')
        self.assertEqual(first, second)
        self.assertEqual(mock_fetch.call_count, 1)

    def test_missing_blocks_do_not_raise(self):
        """A shape change should degrade to nulls, not a 500."""
        stats = self._stats(page_props={'odpMarketAnalytics': {}})
        self.assertIsNone(stats['home_value_index'])
        self.assertIsNone(stats['median_list_price'])
        self.assertEqual(stats['region']['name'], '')

    def test_output_serializes(self):
        from api.serializers import MarketStatsSerializer
        data = MarketStatsSerializer(self._stats(history=True)).data
        self.assertEqual(data['home_value_index'], 507622.43)
        self.assertEqual(data['region']['name'], 'Austin')
        self.assertEqual(data['region']['parentage'][0]['name'], 'United States')
        self.assertTrue(data['history']['home_value_index'])


@override_settings(CACHES=LOCMEM_CACHE)
class MarketStatsSlugResolutionTests(TestCase):
    """
    Slug resolution for /marketStats.

    `/austin-tx/home-values/` resolves but `/90210/home-values/` 404s, so a miss
    falls back to an autocomplete lookup. The literal slug must be tried first —
    resolving eagerly would double the request count on the common path.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def _scraper(self):
        from scrapers.property_scraper import property_scraper
        return property_scraper

    def test_literal_slug_is_tried_first_and_costs_no_lookup(self):
        scraper = self._scraper()
        with patch.object(scraper, '_load_market_page',
                          return_value={'odpMarketAnalytics': {}}) as mock_load, \
             patch.object(scraper, 'autocomplete') as mock_auto:
            props, slug = scraper._fetch_market_page('austin-tx')
        self.assertEqual(slug, 'austin-tx')
        self.assertEqual(mock_load.call_count, 1)
        mock_auto.assert_not_called()

    def test_free_text_location_is_slugified(self):
        scraper = self._scraper()
        with patch.object(scraper, '_load_market_page',
                          return_value={'odpMarketAnalytics': {}}) as mock_load:
            _, slug = scraper._fetch_market_page('Austin, TX')
        self.assertEqual(slug, 'austin-tx')
        self.assertEqual(mock_load.call_args[0][0], 'austin-tx')

    def test_zipcode_falls_back_to_autocomplete(self):
        """`/90210/home-values/` 404s; the city-state-zip form resolves."""
        scraper = self._scraper()
        calls = []

        def fake_load(slug):
            calls.append(slug)
            return {'odpMarketAnalytics': {}} if slug == 'los-angeles-ca-90210' else None

        with patch.object(scraper, '_load_market_page', side_effect=fake_load), \
             patch.object(scraper, 'autocomplete', return_value=[{
                 'type': 'region', 'region_type': 'zipcode', 'city': 'Los Angeles',
                 'state': 'CA', 'zipcode': '90210',
             }]):
            _, slug = scraper._fetch_market_page('90210')

        self.assertEqual(slug, 'los-angeles-ca-90210')
        self.assertEqual(calls[0], '90210')

    def test_state_only_zip_form_is_tried_as_a_second_fallback(self):
        scraper = self._scraper()

        def fake_load(slug):
            return {'odpMarketAnalytics': {}} if slug == 'ca-90210' else None

        with patch.object(scraper, '_load_market_page', side_effect=fake_load), \
             patch.object(scraper, 'autocomplete', return_value=[{
                 'type': 'region', 'region_type': 'zipcode', 'city': 'Los Angeles',
                 'state': 'CA', 'zipcode': '90210',
             }]):
            _, slug = scraper._fetch_market_page('90210')
        self.assertEqual(slug, 'ca-90210')

    def test_unresolvable_location_raises_not_found(self):
        scraper = self._scraper()
        with patch.object(scraper, '_load_market_page', return_value=None), \
             patch.object(scraper, 'autocomplete', return_value=[]):
            with self.assertRaises(NotFoundException):
                scraper._fetch_market_page('zzzqqqxyz')

    def test_autocomplete_failure_does_not_mask_the_not_found(self):
        from scrapers.base import ScraperException
        scraper = self._scraper()
        with patch.object(scraper, '_load_market_page', return_value=None), \
             patch.object(scraper, 'autocomplete', side_effect=ScraperException('boom')):
            with self.assertRaises(NotFoundException):
                scraper._fetch_market_page('somewhere')


class OpenAPISchemaTests(TestCase):
    """
    Guards on the RapidAPI-facing OpenAPI document.

    RapidAPI builds its endpoint groups from the root-level `tags` declaration
    and falls back to the operationId prefix for anything it can't place. An
    import without both produced the three real groups plus one empty group per
    endpoint. These assertions are what keep a newly added endpoint from
    reintroducing that.
    """

    def _schema(self):
        from drf_spectacular.generators import SchemaGenerator
        return SchemaGenerator().get_schema(request=None, public=True)

    def _operations(self, schema):
        methods = {'get', 'put', 'post', 'delete', 'patch', 'head', 'options'}
        for path, item in schema['paths'].items():
            for method, operation in item.items():
                if method.lower() in methods and isinstance(operation, dict):
                    yield path, operation

    def test_root_tags_are_declared(self):
        schema = self._schema()
        names = [t['name'] for t in schema.get('tags', [])]
        self.assertEqual(sorted(names), ['Agents', 'Properties', 'Utilities'])

    def test_every_operation_tag_is_declared_at_root(self):
        """An undeclared tag is what makes RapidAPI invent a fallback group."""
        schema = self._schema()
        declared = {t['name'] for t in schema.get('tags', [])}
        for path, operation in self._operations(schema):
            tags = operation.get('tags') or []
            self.assertTrue(tags, f"{path} has no tag")
            for tag in tags:
                self.assertIn(tag, declared, f"{path} uses undeclared tag {tag!r}")

    def test_operation_ids_have_no_action_suffix(self):
        """`similarHomes_list` becomes a group named 'similarHomes' on import."""
        schema = self._schema()
        for path, operation in self._operations(schema):
            operation_id = operation.get('operationId', '')
            self.assertFalse(
                operation_id.endswith(
                    ('_retrieve', '_list', '_create', '_update', '_destroy',
                     '_partial_update')
                ),
                f"{path} has an unstripped operationId: {operation_id}",
            )

    def test_operation_ids_are_unique(self):
        schema = self._schema()
        ids = [op.get('operationId') for _, op in self._operations(schema)]
        self.assertEqual(len(ids), len(set(ids)), 'duplicate operationId in schema')

    def test_similar_homes_exposes_ranking_fields(self):
        """The comp ranking fields must reach the published schema."""
        schema = self._schema()
        properties = schema['components']['schemas']['SimilarHome']['properties']
        self.assertIn('distance_miles', properties)
        self.assertIn('similarity_score', properties)

    def test_autocomplete_exposes_zpid(self):
        schema = self._schema()
        properties = schema['components']['schemas']['AutocompleteSuggestion']['properties']
        self.assertIn('zpid', properties)
        self.assertIn('region_id', properties)


class AutocompleteScraperTests(TestCase):
    """
    Tests for the rewritten autocomplete parser.

    Shapes here are copied from live responses of Zillow's suggestions endpoint
    (2026-07-30). The old implementation POSTed an unsafelisted GraphQL query,
    always failed, and returned a stub echoing the input as a city name — these
    assert on real fields so that regression is visible.
    """

    def _scraper(self):
        from scrapers.property_scraper import property_scraper
        return property_scraper

    @staticmethod
    def _response(payload):
        response = MagicMock()
        response.json.return_value = payload
        return response

    REGION_HIT = {
        'display': 'Seattle, WA', 'resultType': 'Region',
        'metaData': {'regionId': 16037, 'regionType': 'city', 'city': 'Seattle',
                     'county': 'King County', 'state': 'WA',
                     'lat': 47.618, 'lng': -122.351},
    }
    ADDRESS_HIT = {
        'display': '1006 Hollybluff St Austin, TX 78753', 'resultType': 'Address',
        'metaData': {'addressType': 'forsale_address', 'streetNumber': '1006',
                     'streetName': 'Hollybluff St', 'city': 'Austin', 'state': 'TX',
                     'zipCode': '78753', 'zpid': 29429121,
                     'lat': 30.367, 'lng': -97.675},
    }

    def test_parses_region_hit(self):
        scraper = self._scraper()
        with patch.object(scraper, 'get',
                          return_value=self._response({'results': [self.REGION_HIT]})):
            result = scraper.autocomplete('seattle')
        self.assertEqual(len(result), 1)
        hit = result[0]
        self.assertEqual(hit['type'], 'region')
        self.assertEqual(hit['region_id'], 16037)
        self.assertEqual(hit['region_type'], 'city')
        self.assertEqual(hit['county'], 'King County')
        self.assertEqual(hit['latitude'], 47.618)
        self.assertIsNone(hit['zpid'])
        # `id` has always been the region id — keep it that way for existing callers.
        self.assertEqual(hit['id'], '16037')

    def test_parses_address_hit_with_zpid(self):
        scraper = self._scraper()
        with patch.object(scraper, 'get',
                          return_value=self._response({'results': [self.ADDRESS_HIT]})):
            result = scraper.autocomplete('1006 Hollybluff')
        hit = result[0]
        self.assertEqual(hit['type'], 'address')
        self.assertEqual(hit['zpid'], 29429121)
        self.assertEqual(hit['zipcode'], '78753')
        self.assertEqual(hit['address_type'], 'forsale_address')
        self.assertIsNone(hit['region_id'])

    def test_no_match_returns_empty_not_a_fake_city(self):
        """The old stub turned '9021' into a city named '9021'."""
        scraper = self._scraper()
        with patch.object(scraper, 'get',
                          return_value=self._response({'results': []})):
            self.assertEqual(scraper.autocomplete('zzzqqqxyz'), [])

    def test_request_failure_raises(self):
        from scrapers.base import ScraperException
        scraper = self._scraper()
        with patch.object(scraper, 'get', side_effect=ScraperException('boom')):
            with self.assertRaises(ScraperException):
                scraper.autocomplete('seattle')

    def test_output_serializes(self):
        from api.serializers import AutocompleteSuggestionSerializer
        scraper = self._scraper()
        with patch.object(scraper, 'get', return_value=self._response(
                {'results': [self.REGION_HIT, self.ADDRESS_HIT]})):
            result = scraper.autocomplete('seattle')
        data = AutocompleteSuggestionSerializer(result, many=True).data
        self.assertEqual(data[0]['region_id'], 16037)
        self.assertEqual(data[1]['zpid'], 29429121)

    def test_uses_the_session_pool_not_bare_requests(self):
        """
        Scraping paths must go through BaseScraper.get (curl_cffi + proxy +
        impersonation). The old implementation called `requests.post` directly,
        which bypasses all three and gets blocked.
        """
        scraper = self._scraper()
        with patch.object(scraper, 'get',
                          return_value=self._response({'results': []})) as mock_get:
            scraper.autocomplete('seattle')
        mock_get.assert_called_once()
        self.assertIn('autocomplete/v3/suggestions', mock_get.call_args[0][0])


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
            {'zpid': 22222222, 'address': '456 Oak Ave', 'price': 720000.0,
             'distance_miles': 0.21, 'similarity_score': 0.34},
        ]
        response = self.client.get('/similarHomes', {'zpid': '12345678'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['zpid'], 22222222)
        self.assertEqual(response.data[0]['distance_miles'], 0.21)
        mock_scraper.get_similar_homes.assert_called_once_with(
            12345678, count=8, list_type=None
        )

    @patch('api.views.property_scraper')
    def test_similar_homes_passes_count_and_list_type(self, mock_scraper):
        mock_scraper.get_similar_homes.return_value = []
        response = self.client.get(
            '/similarHomes', {'zpid': '12345678', 'count': '3', 'listType': 'sold'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_scraper.get_similar_homes.assert_called_once_with(
            12345678, count=3, list_type='sold'
        )

    def test_similar_homes_rejects_bad_list_type(self):
        response = self.client.get(
            '/similarHomes', {'zpid': '12345678', 'listType': 'for-lease'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)

    @patch('api.views.property_scraper')
    def test_market_stats_endpoint(self, mock_scraper):
        mock_scraper.get_market_stats.return_value = {
            'region': {'id': 10221, 'name': 'Austin', 'type': 'city',
                       'url': '', 'slug': 'austin-tx', 'parentage': []},
            'listings_as_of': '2026-06-30', 'sales_as_of': '2026-05-31',
            'home_value_index': 507622.43, 'home_value_index_yoy_pct': -5.02,
            'benchmarks': {},
        }
        response = self.client.get('/marketStats', {'location': 'austin-tx'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['home_value_index'], 507622.43)
        self.assertEqual(response.data['region']['name'], 'Austin')
        mock_scraper.get_market_stats.assert_called_once_with(
            'austin-tx', history=False
        )

    def test_market_stats_requires_location(self):
        response = self.client.get('/marketStats')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)

    @patch('api.views.property_scraper')
    def test_market_stats_history_flag(self, mock_scraper):
        mock_scraper.get_market_stats.return_value = {
            'region': {}, 'benchmarks': {}, 'history': {},
        }
        for raw in ('true', 'True', '1', 'yes'):
            mock_scraper.get_market_stats.reset_mock()
            self.client.get('/marketStats', {'location': 'austin-tx', 'history': raw})
            self.assertEqual(
                mock_scraper.get_market_stats.call_args.kwargs['history'], True,
                f"history={raw!r} should enable the series",
            )

        mock_scraper.get_market_stats.reset_mock()
        self.client.get('/marketStats', {'location': 'austin-tx', 'history': 'false'})
        self.assertEqual(
            mock_scraper.get_market_stats.call_args.kwargs['history'], False
        )

    def test_similar_homes_rejects_out_of_range_count(self):
        response = self.client.get(
            '/similarHomes', {'zpid': '12345678', 'count': '99'}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)


@override_settings(CACHES=LOCMEM_CACHE)
class ExtendedDetailScraperTests(TestCase):
    """Tests for the detail parsers added on top of the shared property cache.

    Key names and shapes here were taken from live Zillow homedetails responses
    (see the fixture), not guessed.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.fixture = _load_property_fixture()

    def _scraper(self):
        from scrapers.property_scraper import property_scraper
        return property_scraper

    def _with_fixture(self, method, *args, **kwargs):
        scraper = self._scraper()
        with patch.object(scraper, '_get_property_data', return_value=self.fixture):
            return getattr(scraper, method)(12345678, *args, **kwargs)

    def test_all_detail_parsers_share_one_fetch(self):
        """Every new endpoint reads the cached object; none adds a second fetch."""
        scraper = self._scraper()
        soup = _homedetails_soup(self.fixture)
        with patch.object(scraper, 'get_soup', return_value=soup) as mock_soup:
            scraper.get_open_houses(12345678)
            scraper.get_listing_agent(12345678)
            scraper.get_monthly_cost(12345678)
            scraper.get_home_facts(12345678)
            scraper.get_tax_assessment(12345678)
            scraper.get_nearby_areas(12345678)
            scraper.get_listing_status(12345678)
        self.assertEqual(mock_soup.call_count, 1)

    def test_get_open_houses(self):
        from api.serializers import OpenHouseSerializer
        events = self._with_fixture('get_open_houses')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['display_text'], 'Sun. 12-2pm')
        self.assertEqual(events[0]['description'], 'Refreshments provided')
        # Epoch-ms timestamps are rendered as ISO-8601 Z strings.
        self.assertTrue(events[0]['start_time'].endswith('Z'))
        self.assertTrue(events[0]['start_time'].startswith('2024-03-06'))
        self.assertEqual(OpenHouseSerializer(events, many=True).data[0]['end_time'],
                         events[0]['end_time'])

    def test_get_open_houses_empty_is_not_an_error(self):
        """Most listings carry an empty schedule; that must yield []."""
        scraper = self._scraper()
        data = dict(self.fixture, openHouseSchedule=[])
        with patch.object(scraper, '_get_property_data', return_value=data):
            self.assertEqual(scraper.get_open_houses(12345678), [])

    def test_get_listing_agent(self):
        from api.serializers import ListingAgentSerializer
        agent = self._with_fixture('get_listing_agent')
        self.assertEqual(agent['agent_name'], 'Enrique Cordova')
        self.assertEqual(agent['agent_phone'], '(512) 744-5275')
        self.assertEqual(agent['broker_name'], 'Acme Realty')
        self.assertEqual(agent['broker_phone'], '(877) 366-2213')
        self.assertEqual(agent['listing_offices'], ['Acme Realty'])
        self.assertEqual(agent['mls_id'], 'ABC123')
        # attributionInfo has no co-agent, so listedBy supplies it.
        self.assertEqual(agent['co_agent_name'], 'Lesley Estes')
        self.assertEqual(ListingAgentSerializer(agent).data['agent_name'],
                         'Enrique Cordova')

    def test_get_listing_agent_falls_back_to_listed_by(self):
        """A listing with no attributionInfo still resolves names from listedBy."""
        scraper = self._scraper()
        data = dict(self.fixture, attributionInfo={})
        with patch.object(scraper, '_get_property_data', return_value=data):
            agent = scraper.get_listing_agent(12345678)
        self.assertEqual(agent['agent_name'], 'Enrique Cordova')
        self.assertEqual(agent['broker_name'], 'Acme Realty')

    def test_hoa_normalized_to_monthly(self):
        """A semi-annual resoFacts fee must not be reported as a monthly one."""
        scraper = self._scraper()
        data = dict(self.fixture)
        data.pop('monthlyHoaFee')  # force the resoFacts string path
        with patch.object(scraper, '_get_property_data', return_value=data):
            cost = scraper.get_monthly_cost(12345678)
        self.assertEqual(cost['hoa_fee'], 50.0)  # "$300 semi-annually" / 6

    def test_get_monthly_cost_amortization(self):
        from api.serializers import MonthlyCostSerializer
        cost = self._with_fixture('get_monthly_cost')
        self.assertEqual(cost['down_payment'], 150000.0)
        self.assertEqual(cost['loan_amount'], 600000.0)
        self.assertEqual(cost['interest_rate'], 7.07)  # live 30-yr rate
        self.assertEqual(cost['rate_source'], 'ZGMI')
        # 600000 @ 7.07% over 360 months.
        self.assertAlmostEqual(cost['principal_and_interest'], 4020.06, places=2)
        self.assertEqual(cost['property_tax'], 912.5)   # taxAnnualAmount / 12
        self.assertEqual(cost['home_insurance'], 218.75)  # 0.35% of price / 12
        self.assertEqual(cost['hoa_fee'], 50.0)
        self.assertIsNone(cost['mortgage_insurance'])  # 20% down
        self.assertAlmostEqual(
            cost['total_monthly'],
            cost['principal_and_interest'] + 912.5 + 218.75 + 50.0, places=1
        )
        self.assertEqual(MonthlyCostSerializer(cost).data['total_monthly'],
                         cost['total_monthly'])

    def test_get_monthly_cost_adds_pmi_under_20_percent_down(self):
        cost = self._with_fixture('get_monthly_cost', down_payment_percent=10.0)
        self.assertEqual(cost['loan_amount'], 675000.0)
        self.assertEqual(cost['mortgage_insurance'], 281.25)  # 0.5%/yr of loan
        self.assertIn('mortgage_insurance', cost['estimated_fields'])

    def test_get_monthly_cost_honours_overrides(self):
        cost = self._with_fixture('get_monthly_cost', term_years=15,
                                  interest_rate=5.0)
        self.assertEqual(cost['term_years'], 15)
        self.assertEqual(cost['interest_rate'], 5.0)
        self.assertAlmostEqual(cost['principal_and_interest'], 4744.76, places=2)

    def test_get_home_facts_drops_nulls(self):
        from api.serializers import HomeFactsSerializer
        facts = self._with_fixture('get_home_facts')
        self.assertIn('appliances', facts['facts'])
        # null / "" / [] entries are stripped so callers only see populated keys.
        for empty_key in ('flooring', 'attic', 'buildingFeatures'):
            self.assertNotIn(empty_key, facts['facts'])
        self.assertEqual(facts['fact_count'], len(facts['facts']))
        self.assertEqual(facts['at_a_glance']['Year Built'], '2015')
        self.assertEqual(HomeFactsSerializer(facts).data['fact_count'],
                         facts['fact_count'])

    def test_get_tax_assessment(self):
        from api.serializers import TaxAssessmentSerializer
        tax = self._with_fixture('get_tax_assessment')
        self.assertEqual(tax['tax_assessed_value'], 730000)
        self.assertEqual(tax['tax_annual_amount'], 10950)
        self.assertEqual(tax['property_tax_rate'], 1.46)
        self.assertEqual(tax['effective_tax_rate'], 1.5)  # 10950 / 730000
        self.assertEqual(tax['parcel_id'], '862650')
        self.assertEqual(tax['county_fips'], '48453')
        self.assertEqual(tax['zoning'], 'SF-3')
        self.assertEqual(TaxAssessmentSerializer(tax).data['county'], 'Travis County')

    def test_get_nearby_areas(self):
        from api.serializers import NearbyAreasSerializer
        areas = self._with_fixture('get_nearby_areas')
        self.assertEqual(len(areas['cities']), 2)
        self.assertEqual(areas['cities'][0]['name'], 'Austin')
        self.assertEqual(areas['cities'][0]['path'], '/austin-tx/')
        self.assertEqual(areas['cities'][0]['url'],
                         'https://www.zillow.com/austin-tx/')
        self.assertEqual(areas['zipcodes'][0]['name'], '78704')
        self.assertEqual(NearbyAreasSerializer(areas).data['neighborhoods'][0]['name'],
                         'Downtown')

    def test_get_listing_status(self):
        from api.serializers import ListingStatusSerializer
        s = self._with_fixture('get_listing_status')
        self.assertEqual(s['status'], 'FOR_SALE')
        self.assertEqual(s['listing_type'], 'For Sale by Agent')
        # A price cut must stay negative; clean_price strips the sign.
        self.assertEqual(s['price_change'], -15000)
        self.assertEqual(s['price_change_date'], '2024-03-01')
        self.assertTrue(s['is_fsba'])
        self.assertFalse(s['is_foreclosure'])
        self.assertFalse(s['is_pending'])
        self.assertEqual(s['time_on_zillow'], '12 days')
        self.assertEqual(ListingStatusSerializer(s).data['price_change'], -15000)


@override_settings(CACHES=LOCMEM_CACHE)
class ExtendedDetailEndpointTests(APITestCase):
    """Routing and validation for the endpoints added on the shared cache."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_all_new_endpoints_require_zpid(self):
        for path in ('/openHouses', '/listingAgent', '/monthlyCost', '/homeFacts',
                     '/taxAssessment', '/nearbyAreas', '/listingStatus'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data['status_code'], 400)

    @patch('api.views.property_scraper')
    def test_open_houses_endpoint(self, mock_scraper):
        mock_scraper.get_open_houses.return_value = [
            {'start_time': '2024-03-06T18:00:00Z', 'display_text': 'Sun. 12-2pm'},
        ]
        response = self.client.get('/openHouses', {'zpid': '12345678'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]['display_text'], 'Sun. 12-2pm')

    @patch('api.views.property_scraper')
    def test_listing_agent_endpoint(self, mock_scraper):
        mock_scraper.get_listing_agent.return_value = {
            'zpid': 12345678, 'agent_name': 'Enrique Cordova', 'mls_id': 'ABC123',
        }
        response = self.client.get('/listingAgent', {'zpid': '12345678'})
        self.assertEqual(response.data['agent_name'], 'Enrique Cordova')
        mock_scraper.get_listing_agent.assert_called_once_with(12345678)

    @patch('api.views.property_scraper')
    def test_monthly_cost_passes_params(self, mock_scraper):
        mock_scraper.get_monthly_cost.return_value = {'zpid': 12345678}
        response = self.client.get('/monthlyCost', {
            'zpid': '12345678', 'downPayment': '10', 'termYears': '15',
            'interestRate': '5.5',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_scraper.get_monthly_cost.assert_called_once_with(
            12345678, down_payment_percent=10.0, term_years=15, interest_rate=5.5
        )

    @patch('api.views.property_scraper')
    def test_monthly_cost_defaults(self, mock_scraper):
        mock_scraper.get_monthly_cost.return_value = {'zpid': 12345678}
        self.client.get('/monthlyCost', {'zpid': '12345678'})
        mock_scraper.get_monthly_cost.assert_called_once_with(
            12345678, down_payment_percent=20.0, term_years=30, interest_rate=None
        )

    def test_monthly_cost_rejects_bad_down_payment(self):
        response = self.client.get('/monthlyCost',
                                   {'zpid': '12345678', 'downPayment': '150'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)

    def test_monthly_cost_rejects_bad_term(self):
        response = self.client.get('/monthlyCost',
                                   {'zpid': '12345678', 'termYears': '7'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status_code'], 400)

    @patch('api.views.property_scraper')
    def test_home_facts_endpoint(self, mock_scraper):
        mock_scraper.get_home_facts.return_value = {
            'zpid': 12345678, 'fact_count': 2, 'at_a_glance': {'Type': 'SFR'},
            'facts': {'appliances': ['Dishwasher'], 'cooling': ['Central Air']},
        }
        response = self.client.get('/homeFacts', {'zpid': '12345678'})
        self.assertEqual(response.data['fact_count'], 2)
        self.assertEqual(response.data['facts']['appliances'], ['Dishwasher'])

    @patch('api.views.property_scraper')
    def test_tax_assessment_endpoint(self, mock_scraper):
        mock_scraper.get_tax_assessment.return_value = {
            'zpid': 12345678, 'tax_assessed_value': 730000.0, 'parcel_id': '862650',
        }
        response = self.client.get('/taxAssessment', {'zpid': '12345678'})
        self.assertEqual(response.data['tax_assessed_value'], 730000.0)

    @patch('api.views.property_scraper')
    def test_nearby_areas_endpoint(self, mock_scraper):
        mock_scraper.get_nearby_areas.return_value = {
            'zpid': 12345678,
            'cities': [{'name': 'Austin', 'path': '/austin-tx/', 'url': 'u'}],
            'neighborhoods': [], 'zipcodes': [],
        }
        response = self.client.get('/nearbyAreas', {'zpid': '12345678'})
        self.assertEqual(response.data['cities'][0]['name'], 'Austin')

    @patch('api.views.property_scraper')
    def test_listing_status_endpoint(self, mock_scraper):
        mock_scraper.get_listing_status.return_value = {
            'zpid': 12345678, 'status': 'FOR_SALE', 'price_change': -15000.0,
            'is_foreclosure': False,
        }
        response = self.client.get('/listingStatus', {'zpid': '12345678'})
        self.assertEqual(response.data['price_change'], -15000.0)
        self.assertFalse(response.data['is_foreclosure'])


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


@override_settings(CACHES=LOCMEM_CACHE)
class ByAddressTests(APITestCase):
    """Tests for the /byAddress endpoint."""

    def setUp(self):
        # /byAddress is wrapped in cache_page, which writes to the default
        # (Redis) cache. Without an in-process cache that is cleared per test,
        # a response cached by an earlier run is replayed and the scraper mock
        # is never called. Same reason as PropertyDetailEndpointTests.
        from django.core.cache import cache
        cache.clear()

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
