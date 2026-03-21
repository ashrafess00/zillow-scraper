"""
Base scraper class with common functionality.
"""

import time
import random
import logging
from typing import Optional, Dict, Any
from urllib.parse import urljoin

from curl_cffi import requests
from curl_cffi.requests.errors import RequestsError
from bs4 import BeautifulSoup
from django.conf import settings

from core.proxy_manager import proxy_manager
from core.user_agent_manager import user_agent_manager

logger = logging.getLogger(__name__)


class ScraperException(Exception):
    """Base exception for scraper errors."""
    pass


class BlockedException(ScraperException):
    """Raised when the scraper is blocked by the target site."""
    pass


class NotFoundException(ScraperException):
    """Raised when the requested resource is not found."""
    pass


class BaseScraper:
    """
    Base scraper class with proxy rotation, user-agent rotation,
    request delays, and retry logic.
    """
    
    BASE_URL = "https://www.zillow.com"
    
    def __init__(self):
        # Don't use a persistent session - create fresh connections
        # This allows rotating proxies to give new IPs per request
        self.use_session = False
        
        scraper_settings = getattr(settings, 'SCRAPER_SETTINGS', {})
        self.delay_min = scraper_settings.get('REQUEST_DELAY_MIN', 1.0)
        self.delay_max = scraper_settings.get('REQUEST_DELAY_MAX', 3.0)
        self.timeout = scraper_settings.get('REQUEST_TIMEOUT', 30)
        self.max_retries = scraper_settings.get('MAX_RETRIES', 3)
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers with a random user agent."""
        return {
            'User-Agent': user_agent_manager.get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'close',  # Don't keep connection alive for rotating proxy
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        }
    
    def _delay(self):
        """Introduce a random delay between requests."""
        delay = random.uniform(self.delay_min, self.delay_max)
        logger.debug(f"Waiting {delay:.2f} seconds before request")
        time.sleep(delay)
    
    def _make_request(
        self,
        url: str,
        method: str = 'GET',
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        use_proxy: bool = True,
        retry_count: int = 0,
    ) -> requests.Response:
        """
        Make an HTTP request with retry logic.
        
        Args:
            url: Target URL
            method: HTTP method (GET, POST, etc.)
            params: Query parameters
            data: Form data
            json_data: JSON payload
            use_proxy: Whether to use proxy rotation
            retry_count: Current retry attempt
            
        Returns:
            Response object
            
        Raises:
            ScraperException: If request fails after all retries
        """
        if retry_count > 0:
            self._delay()
        
        # headers = self._get_headers() # Removed: curl_cffi requires its own specific headers to match the TLS signature
        proxies = proxy_manager.get_proxy() if use_proxy else None
        
        try:
            # Use curl_cffi directly to impersonate a browser's TLS signature
            response = requests.request(
                method=method,
                url=url,
                params=params,
                data=data,
                json=json_data,
                # headers=headers, # Do not pass conflicting headers 
                proxies=proxies,
                timeout=self.timeout,
                impersonate="chrome"
            )
            
            # Check for blocking
            if response.status_code == 403:
                raise BlockedException("Request blocked by Zillow (403 Forbidden)")
            
            if response.status_code == 429:
                raise BlockedException("Rate limited by Zillow (429 Too Many Requests)")
            
            if response.status_code == 404:
                raise NotFoundException(f"Resource not found: {url}")
            
            response.raise_for_status()
            
            # Mark proxy as successful
            if proxies:
                proxy_manager.mark_proxy_success(proxies.get('http', ''))
            
            return response
            
        except (RequestsError, BlockedException) as e:
            # Mark proxy as failed
            if proxies:
                proxy_manager.mark_proxy_failed(proxies.get('http', ''))
            
            if retry_count < self.max_retries:
                logger.warning(
                    f"Request failed (attempt {retry_count + 1}/{self.max_retries}): {e}"
                )
                return self._make_request(
                    url=url,
                    method=method,
                    params=params,
                    data=data,
                    json_data=json_data,
                    use_proxy=use_proxy,
                    retry_count=retry_count + 1,
                )
            
            logger.error(f"Request failed after {self.max_retries} retries: {e}")
            raise ScraperException(f"Failed to fetch {url}: {e}")
    
    def get(
        self,
        url: str,
        params: Optional[Dict] = None,
        use_proxy: bool = True,
    ) -> requests.Response:
        """Make a GET request."""
        return self._make_request(url, 'GET', params=params, use_proxy=use_proxy)
    
    def post(
        self,
        url: str,
        data: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        use_proxy: bool = True,
    ) -> requests.Response:
        """Make a POST request."""
        return self._make_request(
            url, 'POST', data=data, json_data=json_data, use_proxy=use_proxy
        )
    
    def get_soup(
        self,
        url: str,
        params: Optional[Dict] = None,
        use_proxy: bool = True,
    ) -> BeautifulSoup:
        """
        Fetch a page and return a BeautifulSoup object.
        
        Args:
            url: Target URL
            params: Query parameters
            use_proxy: Whether to use proxy rotation
            
        Returns:
            BeautifulSoup object
        """
        response = self.get(url, params=params, use_proxy=use_proxy)
        return BeautifulSoup(response.text, 'lxml')
    
    def build_url(self, path: str) -> str:
        """Build a full URL from a relative path."""
        return urljoin(self.BASE_URL, path)
