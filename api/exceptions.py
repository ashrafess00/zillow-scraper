"""
Custom exception handling for the API.
"""

import logging
import traceback
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings

from scrapers.base import NotFoundException, BlockedException, ScraperException

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Custom exception handler that converts scraper exceptions to API responses.
    """
    # Call REST framework's default exception handler first
    response = exception_handler(exc, context)
    
    if response is not None:
        # RapidAPI metric protection: Force all errors to return HTTP 200
        if isinstance(response.data, dict):
            response.data['success'] = False
            if 'status_code' not in response.data:
                response.data['status_code'] = response.status_code
        response.status_code = status.HTTP_200_OK
        return response
    
    # Handle scraper-specific exceptions (expected errors - minimal logging)
    if isinstance(exc, NotFoundException):
        # Return 200 with empty results — the request succeeded but found nothing
        logger.info(f"No results: {exc}")
        return Response(
            {
                'count': 0,
                'results': [],
                'message': str(exc),
                'pagination': {
                    'total_results': 0,
                    'total_pages': 0,
                    'current_page': 1,
                    'per_page': 40,
                    'has_next': False,
                    'has_previous': False,
                },
            },
            status=status.HTTP_200_OK
        )
    
    if isinstance(exc, BlockedException):
        logger.warning(f"Data Unavailable: {exc}")
        return Response(
            {
                'success': False,
                'error': 'Data Unavailable',
                'message': 'The property data is temporarily unavailable. Please try again in a few moments.',
                'status_code': 503,
            },
            status=status.HTTP_200_OK
        )
    
    if isinstance(exc, ScraperException):
        logger.warning(f"Scraper Error: {exc}")
        return Response(
            {
                'success': False,
                'error': 'Data Unavailable',
                'message': 'Unable to retrieve property data at this time. Please verify the URL or try again later.',
                'status_code': 503,
            },
            status=status.HTTP_200_OK
        )
    
    # For any other unhandled exceptions
    logger.exception(f"Unhandled exception: {exc}")
    return Response(
        {
            'success': False,
            'error': 'System Error',
            'message': 'An unexpected error occurred while processing your request. Please try again later.',
            'status_code': 500,
        },
        status=status.HTTP_200_OK
    )
