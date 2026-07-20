"""
URL configuration for the API app.
"""

from django.urls import path
from . import views

from django.views.decorators.cache import cache_page

# Cache timeout in seconds (15 minutes)
CACHE_TTL = 60 * 15

urlpatterns = [
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
    
    # Other endpoints (Cached)
    path('apartmentDetails', cache_page(CACHE_TTL)(views.apartment_details), name='apartment-details'),
    path('autocomplete', cache_page(CACHE_TTL)(views.autocomplete), name='autocomplete'),
]

