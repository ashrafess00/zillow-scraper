"""
Base scraper class with common functionality.
"""

import time
import random
import logging
import threading
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


class SessionPool:
    """
    Manages a pool of warm curl_cffi Sessions.
    
    Key insight: curl_cffi's first request takes ~10s due to TLS handshake +
    PerimeterX challenge cookie generation. Subsequent requests on the same
    session take ~1.3s because TLS tickets and cookies are cached.
    
    This pool pre-warms sessions and reuses them across Django requests.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._sessions = {}  # {proxy_key: {'session': session, 'request_count': int}}
        self._session_lock = threading.Lock()
        # Refresh session after N requests to avoid stale cookies
        self._max_requests_per_session = 50
        logger.info("SessionPool initialized")
    
    def _create_session(self, proxies) -> requests.Session:
        """Create and pre-warm a new curl_cffi session."""

        
        session = requests.Session()
        
        # Pre-warm: hit Zillow homepage to establish TLS + get challenge cookies
        try:
            proxy_log = proxies['http'][:30] + '...' if proxies else 'none'
            logger.info(f"Pre-warming session against Zillow with proxy {proxy_log}")
            t0 = time.time()
            resp = session.get(
                "https://www.zillow.com/",
                impersonate="chrome",
                proxies=proxies,
                timeout=30,
            )
            elapsed = time.time() - t0
            logger.info(
                f"Session pre-warmed in {elapsed:.2f}s "
                f"(HTTP {resp.status_code}, {len(resp.content)} bytes)"
            )
        except Exception as e:
            logger.warning(f"Session pre-warm failed: {e} (will use cold session)")
        
        return session
    
    def get_session(self) -> requests.Session:
        """Get a warm session, creating/refreshing if needed."""
        proxies = proxy_manager.get_proxy()
        proxy_key = proxies['http'] if proxies else 'default'
        
        with self._session_lock:
            session_data = self._sessions.get(proxy_key)
            
            if (
                session_data is None 
                or session_data['request_count'] >= self._max_requests_per_session
            ):
                # Close old session if exists
                if session_data is not None:
                    try:
                        session_data['session'].close()
                    except Exception:
                        pass
                    logger.info(
                        f"Refreshing session after {session_data['request_count']} requests"
                    )
                
                self._sessions[proxy_key] = {
                    'session': self._create_session(proxies),
                    'request_count': 0
                }
                session_data = self._sessions[proxy_key]
            
            session_data['request_count'] += 1
            return session_data['session']
    
    def invalidate(self):
        """Force-refresh the session (e.g., after repeated blocks)."""
        proxies = proxy_manager.get_proxy()
        proxy_key = proxies['http'] if proxies else 'default'
        
        with self._session_lock:
            session_data = self._sessions.get(proxy_key)
            if session_data is not None:
                try:
                    session_data['session'].close()
                except Exception:
                    pass
                del self._sessions[proxy_key]
            logger.info(f"Session invalidated for proxy {proxy_key[:30]}, will re-warm on next request")


# Module-level singleton
session_pool = SessionPool()


class BaseScraper:
    """
    Base scraper class with proxy rotation, user-agent rotation,
    request delays, and retry logic.
    
    Uses a persistent warm curl_cffi Session for fast requests (~1.3s)
    instead of cold one-shot requests (~10s).
    """
    
    BASE_URL = "https://www.zillow.com"
    
    def __init__(self):
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
        Make an HTTP request using the warm session pool.
        
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
        
        proxies = proxy_manager.get_proxy() if use_proxy else None
        session = session_pool.get_session()
        
        try:
            response = session.request(
                method=method,
                url=url,
                params=params,
                data=data,
                json=json_data,
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
            
            # On repeated blocks, invalidate the session to force re-warm
            if retry_count >= 2:
                session_pool.invalidate()
            
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
