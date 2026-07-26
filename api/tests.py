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
