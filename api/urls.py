"""
URL configuration for the API app.
"""

from django.urls import path
from . import views

from django.views.decorators.cache import cache_page

# Cache timeout in seconds (15 minutes)
CACHE_TTL = 60 * 15

urlpatterns = [
    # Health check (never cached — must reflect live state)
    path('health', views.health, name='health'),

    # Agent endpoints (Cached)
    path('agentByLocation', cache_page(CACHE_TTL)(views.agent_by_location), name='agent-by-location'),
    path('agentInfo', cache_page(CACHE_TTL)(views.agent_info), name='agent-info'),
    path('agentReviews', cache_page(CACHE_TTL)(views.agent_reviews), name='agent-reviews'),
    path('agentForSaleProperties', cache_page(CACHE_TTL)(views.agent_for_sale_properties), name='agent-for-sale'),
    path('agentForRentProperties', cache_page(CACHE_TTL)(views.agent_for_rent_properties), name='agent-for-rent'),
    path('agentSoldProperties', cache_page(CACHE_TTL)(views.agent_sold_properties), name='agent-sold'),
    
    # Property search endpoints (Cached)
    path('bylocation', cache_page(CACHE_TTL)(views.by_location), name='by-location'),
    path('bycoordinates', cache_page(CACHE_TTL)(views.by_coordinates), name='by-coordinates'),
    path('bymapbounds', cache_page(CACHE_TTL)(views.by_map_bounds), name='by-map-bounds'),
    path('bymlsid', cache_page(CACHE_TTL)(views.by_mls_id), name='by-mls-id'),
    path('bypolygon', cache_page(CACHE_TTL)(views.by_polygon), name='by-polygon'),
    path('byurl', cache_page(CACHE_TTL)(views.by_url), name='by-url'),
    
    # Property detail endpoints by zpid (Cached; all share one Zillow fetch)
    path('property', cache_page(CACHE_TTL)(views.property_detail), name='property-detail'),
    path('zestimate', cache_page(CACHE_TTL)(views.zestimate), name='zestimate'),
    path('priceHistory', cache_page(CACHE_TTL)(views.price_history), name='price-history'),
    path('taxHistory', cache_page(CACHE_TTL)(views.tax_history), name='tax-history'),
    path('photos', cache_page(CACHE_TTL)(views.property_photos), name='property-photos'),
    path('schools', cache_page(CACHE_TTL)(views.schools), name='schools'),
    path('similarHomes', cache_page(CACHE_TTL)(views.similar_homes), name='similar-homes'),

    # Other endpoints (Cached)
    path('apartmentDetails', cache_page(CACHE_TTL)(views.apartment_details), name='apartment-details'),
    path('autocomplete', cache_page(CACHE_TTL)(views.autocomplete), name='autocomplete'),
]

