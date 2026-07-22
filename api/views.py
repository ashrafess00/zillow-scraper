"""
API Views for the Zillow scraper.
"""

import logging
from django.conf import settings
from rest_framework import status, serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter, inline_serializer
from drf_spectacular.types import OpenApiTypes

from .serializers import (
    AgentSerializer,
    PropertySerializer,
    ReviewSerializer,
    AutocompleteSuggestionSerializer,
    ApartmentDetailsSerializer,
    ErrorSerializer,
    PaginationMetadataSerializer,
)
from scrapers.agent_scraper import agent_scraper
from scrapers.property_scraper import property_scraper
from scrapers.base import NotFoundException, ScraperException

logger = logging.getLogger(__name__)

# Zillow typically returns 40 results per page
DEFAULT_PER_PAGE = 40


def get_paginated_response_schema(resource_serializer_class, name):
    """Helper to generate paginated response schema."""
    return inline_serializer(
        name=name,
        fields={
            'count': serializers.IntegerField(),
            'results': resource_serializer_class(many=True),
            'pagination': PaginationMetadataSerializer(),
        }
    )


def build_paginated_response(results, total_results, current_page, per_page=DEFAULT_PER_PAGE, max_pages=None):
    """Build a standardized paginated response wrapper."""
    total_pages = max(1, (total_results + per_page - 1) // per_page)
    if max_pages is not None:
        total_pages = min(max_pages, total_pages)
        total_results = min(total_results, max_pages * per_page)
        
    return {
        'count': len(results),
        'results': results,
        'pagination': {
            'total_results': total_results,
            'total_pages': total_pages,
            'current_page': current_page,
            'per_page': per_page,
            'has_next': current_page < total_pages,
            'has_previous': current_page > 1,
        }
    }


# ============================================================================
# Agent Endpoints
# ============================================================================

@extend_schema(
    summary="Get agents by location",
    description="Returns a list of real estate agents in the specified location.",
    parameters=[
        OpenApiParameter(
            name='location',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Location slug (e.g., los-angeles, new-york-ny, miami-fl)',
            required=False,
        ),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(AgentSerializer, 'PaginatedAgentResponse'),
        404: ErrorSerializer,
    },
    tags=['Agents']
)
@api_view(['GET'])
def agent_by_location(request):
    """Get agents by location."""
    location = request.query_params.get('location', 'los-angeles')
    page = int(request.query_params.get('page', 1))
    
    result = agent_scraper.get_agents_by_location(location, page=page)
    
    serializer = AgentSerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page)
    ))


@extend_schema(
    summary="Get agent information",
    description="Returns detailed information about a specific agent.",
    parameters=[
        OpenApiParameter(
            name='agentname',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent screen name',
            required=False,
        ),
        OpenApiParameter(
            name='url',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent profile URL',
            required=False,
        ),
    ],
    responses={
        200: AgentSerializer,
        404: ErrorSerializer,
    },
    tags=['Agents']
)
@api_view(['GET'])
def agent_info(request):
    """Get agent profile information."""
    agentname = request.query_params.get('agentname')
    url = request.query_params.get('url')
    
    if not agentname and not url:
        return Response(
            {'error': 'Bad Request', 'message': 'Either agentname or url is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    result = agent_scraper.get_agent_info(agentname=agentname, url=url)
    serializer = AgentSerializer(result['result'])
    return Response({
        'source_url': result['source_url'],
        'result': serializer.data
    })


@extend_schema(
    summary="Get agent reviews",
    description="Returns reviews for a specific agent.",
    parameters=[
        OpenApiParameter(
            name='agentname',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent screen name',
            required=False,
        ),
        OpenApiParameter(
            name='url',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent profile URL',
            required=False,
        ),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(ReviewSerializer, 'PaginatedReviewResponse'),
        404: ErrorSerializer,
    },
    tags=['Agents']
)
@api_view(['GET'])
def agent_reviews(request):
    """Get agent reviews."""
    agentname = request.query_params.get('agentname')
    url = request.query_params.get('url')
    page = int(request.query_params.get('page', 1))
    
    if not agentname and not url:
        return Response(
            {'error': 'Bad Request', 'message': 'Either agentname or url is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    result = agent_scraper.get_agent_reviews(agentname=agentname, url=url, page=page)
    serializer = ReviewSerializer(result['results'], many=True)
    
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_reviews', 0),
        current_page=result.get('current_page', page)
    ))


@extend_schema(
    summary="Get agent's for-sale properties",
    description="Returns properties currently for sale by a specific agent.",
    parameters=[
        OpenApiParameter(
            name='agentname',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent screen name',
            required=False,
        ),
        OpenApiParameter(
            name='url',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent profile URL',
            required=False,
        ),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Agents']
)
@api_view(['GET'])
def agent_for_sale_properties(request):
    """Get agent's for-sale properties."""
    agentname = request.query_params.get('agentname')
    url = request.query_params.get('url')
    page = int(request.query_params.get('page', 1))
    
    if not agentname and not url:
        return Response(
            {'error': 'Bad Request', 'message': 'Either agentname or url is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    result = agent_scraper.get_agent_properties(
        agentname=agentname, url=url, property_type='for-sale', page=page
    )
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page),
        per_page=result.get('per_page', 40)
    ))


@extend_schema(
    summary="Get agent's rental properties",
    description="Returns properties currently for rent by a specific agent.",
    parameters=[
        OpenApiParameter(
            name='agentname',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent screen name',
            required=False,
        ),
        OpenApiParameter(
            name='url',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent profile URL',
            required=False,
        ),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Agents']
)
@api_view(['GET'])
def agent_for_rent_properties(request):
    """Get agent's rental properties."""
    agentname = request.query_params.get('agentname')
    url = request.query_params.get('url')
    page = int(request.query_params.get('page', 1))
    
    if not agentname and not url:
        return Response(
            {'error': 'Bad Request', 'message': 'Either agentname or url is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    result = agent_scraper.get_agent_properties(
        agentname=agentname, url=url, property_type='for-rent', page=page
    )
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page),
        per_page=result.get('per_page', 40)
    ))


@extend_schema(
    summary="Get agent's sold properties",
    description="Returns properties previously sold by a specific agent.",
    parameters=[
        OpenApiParameter(
            name='agentname',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent screen name',
            required=False,
        ),
        OpenApiParameter(
            name='url',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Agent profile URL',
            required=False,
        ),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Agents']
)
@api_view(['GET'])
def agent_sold_properties(request):
    """Get agent's sold properties."""
    agentname = request.query_params.get('agentname')
    url = request.query_params.get('url')
    page = int(request.query_params.get('page', 1))
    
    if not agentname and not url:
        return Response(
            {'error': 'Bad Request', 'message': 'Either agentname or url is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    result = agent_scraper.get_agent_properties(
        agentname=agentname, url=url, property_type='sold', page=page
    )
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page),
        per_page=result.get('per_page', 40)
    ))


# ============================================================================
# Property Search Endpoints
# ============================================================================

def _get_property_filters(request):
    """Extract property filters from request parameters."""
    filters = {}
    
    # Boolean filters
    bool_params = [
        'isComingSoon', 'isManufactured', 'singleStory', 'isForSaleForeclosure',
        'isSingleFamily', 'isAuction', 'isApartmentOrCondo', 'isCondo',
        'isTownhouse', 'isLotLand', 'isWaterView', 'hasPool', 'isParkView',
        'is3dHome', 'isCityView', 'hasGarage', 'isBasementUnfinished',
        'isBasementFinished', 'isOpenHousesOnly', 'isMountainView', 'isApartment',
        'isMultiFamily',
    ]
    
    for param in bool_params:
        value = request.query_params.get(param)
        if value is not None:
            filters[param] = value.lower() in ('true', '1', 'yes')
    
    # Numeric filters
    num_params = [
        'daysOnZillow', 'page', 'maxPrice', 'beds', 'maxBuilt', 'maxLot',
        'minBuilt', 'minSqft', 'minPrice', 'parkingSpots', 'minLot', 'maxHOA',
        'baths', 'maxSqft',
    ]
    
    for param in num_params:
        value = request.query_params.get(param)
        if value is not None:
            try:
                filters[param] = float(value) if '.' in value else int(value)
            except ValueError:
                pass
    
    return filters


@extend_schema(
    summary="Search properties by location",
    description="Search for properties in a specific location with optional filters.",
    parameters=[
        OpenApiParameter(name='location', type=OpenApiTypes.STR, description='Location slug (e.g., seattle-wa)'),
        OpenApiParameter(name='listType', type=OpenApiTypes.STR, description='Listing type: for-sale, for-rent, sold'),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
        OpenApiParameter(name='beds', type=OpenApiTypes.INT, description='Minimum bedrooms'),
        OpenApiParameter(name='baths', type=OpenApiTypes.INT, description='Minimum bathrooms'),
        OpenApiParameter(name='minPrice', type=OpenApiTypes.NUMBER, description='Minimum price'),
        OpenApiParameter(name='maxPrice', type=OpenApiTypes.NUMBER, description='Maximum price'),
        OpenApiParameter(name='minSqft', type=OpenApiTypes.INT, description='Minimum square footage'),
        OpenApiParameter(name='maxSqft', type=OpenApiTypes.INT, description='Maximum square footage'),
        # Add more as needed
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Properties']
)
@api_view(['GET'])
def by_location(request):
    """Search properties by location."""
    location = request.query_params.get('location', 'seattle-wa')
    list_type = request.query_params.get('listType', 'for-sale')
    page = int(request.query_params.get('page', 1))
    
    filters = _get_property_filters(request)
    filters.pop('page', None)  # Remove page from filters - we pass it explicitly
    
    result = property_scraper.search_by_location(
        location=location,
        list_type=list_type,
        page=page,
        **filters
    )
    
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page),
        max_pages=20
    ))


@extend_schema(
    summary="Search properties by coordinates",
    description="Search for properties near specific coordinates.",
    parameters=[
        OpenApiParameter(name='lat', type=OpenApiTypes.NUMBER, description='Latitude', required=True),
        OpenApiParameter(name='lng', type=OpenApiTypes.NUMBER, description='Longitude', required=True),
        OpenApiParameter(name='listType', type=OpenApiTypes.STR, description='Listing type'),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Properties']
)
@api_view(['GET'])
def by_coordinates(request):
    """Search properties by coordinates."""
    lat = request.query_params.get('lat')
    lng = request.query_params.get('lng')
    
    if not lat or not lng:
        return Response(
            {'error': 'Bad Request', 'message': 'lat and lng are required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    try:
        lat = float(lat)
        lng = float(lng)
    except ValueError:
        return Response(
            {'error': 'Bad Request', 'message': 'lat and lng must be valid numbers', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    list_type = request.query_params.get('listType', 'for-sale')
    page = int(request.query_params.get('page', 1))
    filters = _get_property_filters(request)
    filters.pop('page', None)  # Remove page from filters
    
    result = property_scraper.search_by_coordinates(
        lat=lat, lng=lng, list_type=list_type, page=page, **filters
    )
    
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page),
        max_pages=20
    ))


@extend_schema(
    summary="Search properties by map bounds",
    description="Search for properties within geographic map bounds.",
    parameters=[
        OpenApiParameter(name='north', type=OpenApiTypes.NUMBER, description='Northern latitude bound', required=True),
        OpenApiParameter(name='south', type=OpenApiTypes.NUMBER, description='Southern latitude bound', required=True),
        OpenApiParameter(name='east', type=OpenApiTypes.NUMBER, description='Eastern longitude bound', required=True),
        OpenApiParameter(name='west', type=OpenApiTypes.NUMBER, description='Western longitude bound', required=True),
        OpenApiParameter(name='listType', type=OpenApiTypes.STR, description='Listing type'),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Properties']
)
@api_view(['GET'])
def by_map_bounds(request):
    """Search properties by map bounds."""
    north = request.query_params.get('north')
    south = request.query_params.get('south')
    east = request.query_params.get('east')
    west = request.query_params.get('west')
    
    if not all([north, south, east, west]):
        return Response(
            {'error': 'Bad Request', 'message': 'north, south, east, and west are required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    try:
        north = float(north)
        south = float(south)
        east = float(east)
        west = float(west)
    except ValueError:
        return Response(
            {'error': 'Bad Request', 'message': 'Bounds must be valid numbers', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    list_type = request.query_params.get('listType', 'for-sale')
    page = int(request.query_params.get('page', 1))
    filters = _get_property_filters(request)
    filters.pop('page', None)  # Remove page from filters
    
    result = property_scraper.search_by_map_bounds(
        north=north, south=south, east=east, west=west,
        list_type=list_type, page=page, **filters
    )
    
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page),
        max_pages=20
    ))


@extend_schema(
    summary="Search properties by MLS ID",
    description="Search for properties by MLS listing ID.",
    parameters=[
        OpenApiParameter(name='mlsid', type=OpenApiTypes.STR, description='MLS listing ID', required=True),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Properties']
)
@api_view(['GET'])
def by_mls_id(request):
    """Search properties by MLS ID."""
    mls_id = request.query_params.get('mlsid')
    
    if not mls_id:
        return Response(
            {'error': 'Bad Request', 'message': 'mlsid is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    page = int(request.query_params.get('page', 1))
    filters = _get_property_filters(request)
    filters.pop('page', None)  # Remove page from filters
    
    result = property_scraper.search_by_mls_id(mls_id=mls_id, page=page, **filters)
    
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page),
        max_pages=20
    ))


@extend_schema(
    summary="Search properties by polygon",
    description="Search for properties within a polygon defined by coordinates.",
    parameters=[
        OpenApiParameter(
            name='polygon',
            type=OpenApiTypes.STR,
            description='Semicolon-separated coordinates (lat,lng;lat,lng;...)',
            required=True
        ),
        OpenApiParameter(name='listType', type=OpenApiTypes.STR, description='Listing type'),
        OpenApiParameter(name='page', type=OpenApiTypes.INT, description='Page number'),
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Properties']
)
@api_view(['GET'])
def by_polygon(request):
    """Search properties by polygon."""
    polygon = request.query_params.get('polygon')
    
    if not polygon:
        return Response(
            {'error': 'Bad Request', 'message': 'polygon is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    list_type = request.query_params.get('listType', 'for-sale')
    page = int(request.query_params.get('page', 1))
    filters = _get_property_filters(request)
    filters.pop('page', None)  # Remove page from filters
    
    result = property_scraper.search_by_polygon(
        polygon=polygon, list_type=list_type, page=page, **filters
    )
    
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', page),
        max_pages=20
    ))


@extend_schema(
    summary="Parse properties from Zillow URL",
    description="Parse and return properties from a Zillow search URL.",
    parameters=[
        OpenApiParameter(name='url', type=OpenApiTypes.STR, description='Zillow search URL', required=True),
    ],
    responses={
        200: get_paginated_response_schema(PropertySerializer, 'PaginatedPropertyResponse'),
        404: ErrorSerializer,
    },
    tags=['Properties']
)
@api_view(['GET'])
def by_url(request):
    """Parse properties from a Zillow URL."""
    url = request.query_params.get('url')
    
    if not url:
        return Response(
            {'error': 'Bad Request', 'message': 'url is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    result = property_scraper.search_by_url(url=url)
    
    # Return single object for property detail URLs
    if '/homedetails/' in url and result.get('results'):
        serializer = PropertySerializer(result['results'][0])
        return Response(serializer.data)
    
    # Return paginated response for search URLs
    serializer = PropertySerializer(result['results'], many=True)
    return Response(build_paginated_response(
        results=serializer.data,
        total_results=result.get('total_results', len(result['results'])),
        current_page=result.get('current_page', 1),
        max_pages=20
    ))


# ============================================================================
# Other Endpoints
# ============================================================================

@extend_schema(
    summary="Get apartment details",
    description="Get detailed information about an apartment or building.",
    parameters=[
        OpenApiParameter(name='url', type=OpenApiTypes.STR, description='Apartment listing URL', required=True),
    ],
    responses={
        200: ApartmentDetailsSerializer,
        404: ErrorSerializer,
    },
    tags=['Properties']
)
@api_view(['GET'])
def apartment_details(request):
    """Get apartment details."""
    url = request.query_params.get('url')
    
    if not url:
        return Response(
            {'error': 'Bad Request', 'message': 'url is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    details = property_scraper.get_apartment_details(url=url)
    serializer = ApartmentDetailsSerializer(details)
    return Response(serializer.data)


@extend_schema(
    summary="Location autocomplete",
    description="Get autocomplete suggestions for a location query.",
    parameters=[
        OpenApiParameter(name='q', type=OpenApiTypes.STR, description='Search query', required=True),
    ],
    responses={
        200: AutocompleteSuggestionSerializer(many=True),
        404: ErrorSerializer,
    },
    tags=['Utilities']
)
@api_view(['GET'])
def autocomplete(request):
    """Get location autocomplete suggestions."""
    query = request.query_params.get('q')
    
    if not query:
        return Response(
            {'error': 'Bad Request', 'message': 'q is required', 'status_code': 400},
            status=status.HTTP_200_OK
        )
    
    suggestions = property_scraper.autocomplete(query=query)
    serializer = AutocompleteSuggestionSerializer(suggestions, many=True)
    return Response(serializer.data)


@extend_schema(
    summary="Health check",
    description=(
        "Lightweight liveness/readiness probe. Reports the status of the Redis cache "
        "and the database. Always returns HTTP 200; check the `status` field "
        "(`ok` or `degraded`) and the per-component results in `checks`."
    ),
    responses={
        200: inline_serializer(
            name='HealthResponse',
            fields={
                'status': serializers.CharField(),
                'version': serializers.CharField(),
                'timestamp': serializers.CharField(),
                'checks': serializers.DictField(),
            }
        ),
    },
    tags=['Utilities']
)
@api_view(['GET'])
def health(request):
    """Report service health (cache + database)."""
    from datetime import datetime, timezone as dt_timezone
    from django.core.cache import cache
    from django.db import connection

    checks = {}

    try:
        cache.set('health_check', 'ok', 10)
        checks['cache'] = 'ok' if cache.get('health_check') == 'ok' else 'error: read-back failed'
    except Exception as e:
        logger.warning("Health check: cache failed: %s", e)
        checks['cache'] = f'error: {e}'

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        checks['database'] = 'ok'
    except Exception as e:
        logger.warning("Health check: database failed: %s", e)
        checks['database'] = f'error: {e}'

    overall = 'ok' if all(v == 'ok' for v in checks.values()) else 'degraded'

    return Response({
        'status': overall,
        'version': settings.SPECTACULAR_SETTINGS.get('VERSION', 'unknown'),
        'timestamp': datetime.now(dt_timezone.utc).isoformat(),
        'checks': checks,
    })


@api_view(['GET'])
def debug_fetch(request):
    """
    Debug endpoint to fetch and inspect raw HTML from Zillow.
    Returns page title, element counts, and sample of page structure.
    Also shows proxy configuration.
    """
    from scrapers.base import BaseScraper
    from core.proxy_manager import proxy_manager
    from django.conf import settings
    import requests
    
    url = request.query_params.get('url', 'https://www.zillow.com/professionals/real-estate-agent-reviews/los-angeles/')
    
    # First, show proxy configuration
    proxies_config = settings.SCRAPER_SETTINGS.get('PROXIES', [])
    proxy_to_use = proxy_manager.get_proxy()
    
    proxy_info = {
        'proxies_configured': len(proxy_manager.proxies),
        'proxies_list': [p[:30] + '...' if len(p) > 30 else p for p in proxy_manager.proxies],
        'proxy_being_used': {k: v[:30] + '...' if v and len(v) > 30 else v for k, v in (proxy_to_use or {}).items()},
    }
    
    # Test proxy with a simple httpbin request first
    proxy_test_result = None
    if proxy_to_use:
        try:
            test_response = requests.get(
                'https://httpbin.org/ip',
                proxies=proxy_to_use,
                timeout=15
            )
            proxy_test_result = {
                'httpbin_status': test_response.status_code,
                'ip_info': test_response.json() if test_response.status_code == 200 else None,
                'proxy_works': True
            }
        except Exception as e:
            proxy_test_result = {
                'error': str(e),
                'proxy_works': False
            }
    
    # Now try to fetch from Zillow
    try:
        scraper = BaseScraper()
        soup = scraper.get_soup(url)
        
        # Get page info
        title = soup.find('title')
        body = soup.find('body')
        
        # Count elements
        all_links = soup.find_all('a')
        profile_links = [a for a in all_links if '/profile/' in a.get('href', '')]
        
        # Find script tags with data
        scripts_with_data = []
        for script in soup.find_all('script'):
            text = script.string or ''
            if len(text) > 500:
                patterns = ['__INITIAL_STATE__', '__PRELOADED_STATE__', 'searchResults', 'professionals']
                found_patterns = [p for p in patterns if p in text]
                if found_patterns:
                    scripts_with_data.append({
                        'length': len(text),
                        'patterns_found': found_patterns,
                        'preview': text[:300] + '...'
                    })
        
        # Get sample of body structure
        body_classes = []
        if body:
            for elem in body.find_all(class_=True)[:20]:
                classes = elem.get('class', [])
                if classes:
                    body_classes.extend(classes)
        
        return Response({
            'url': url,
            'proxy_info': proxy_info,
            'proxy_test': proxy_test_result,
            'page_title': title.get_text() if title else 'No title',
            'total_elements': len(soup.find_all()),
            'total_links': len(all_links),
            'profile_links': len(profile_links),
            'profile_link_samples': [a.get('href') for a in profile_links[:5]],
            'scripts_with_data': scripts_with_data,
            'sample_classes': list(set(body_classes))[:30],
            'status': 'success'
        })
        
    except Exception as e:
        return Response({
            'url': url,
            'proxy_info': proxy_info,
            'proxy_test': proxy_test_result,
            'error': str(e),
            'status': 'error'
        }, status=500)


@api_view(['GET'])
def debug_html(request):
    """
    Debug endpoint to fetch and return the raw HTML from a Zillow URL.
    Returns the full HTML content for manual inspection.
    """
    from scrapers.base import BaseScraper
    from django.http import HttpResponse
    
    url = request.query_params.get('url', 'https://www.zillow.com/professionals/real-estate-agent-reviews/los-angeles/')
    
    try:
        scraper = BaseScraper()
        response = scraper.get(url)
        
        # Return raw HTML
        return HttpResponse(
            response.text,
            content_type='text/html',
        )
        
    except Exception as e:
        return Response({
            'url': url,
            'error': str(e),
            'status': 'error'
        }, status=500)
