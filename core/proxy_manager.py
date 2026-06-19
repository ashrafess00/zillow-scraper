"""
Proxy Manager for providing proxy configuration.

Note: If using a rotating proxy provider (like Bright Data, Oxylabs, etc.),
the provider handles IP rotation automatically on each request.
"""

import logging
from typing import Optional, Dict
from django.conf import settings
from core.middleware import get_current_request

logger = logging.getLogger(__name__)


class ProxyManager:
    """
    Provides proxy configuration for web scraping requests.
    
    This is a simplified version that assumes the proxy provider
    handles rotation automatically. No blacklisting or rotation needed.
    """
    
    def __init__(self):
        pass
        
    def _load_proxy(self, proxy_type: str = 'PROXIES') -> Optional[str]:
        """Load proxy from settings."""
        proxies = settings.SCRAPER_SETTINGS.get(proxy_type, [])
        if isinstance(proxies, str):
            proxies = [p.strip() for p in proxies.split(',') if p.strip()]
        
        if proxies:
            # Use the first proxy URL (provider handles rotation)
            return proxies[0] if proxies else None
        return None
    
    def get_proxy(self) -> Optional[Dict[str, str]]:
        """
        Get the configured proxy dynamically based on the current request host.
        
        Returns:
            Dictionary with 'http' and 'https' keys, or None if no proxy configured.
        """
        proxy_type = 'PROXIES'
        request = get_current_request()
        
        if request:
            host = request.get_host()
            
            # --- DEBUG LOGGING ---
            logger.info(f"DEBUG: request.get_host() = {host}")
            
            # Print specific headers that proxies commonly set
            rapidapi_host = request.headers.get('x-rapidapi-host', '')
            forwarded_host = request.headers.get('x-forwarded-host', '')
            logger.info(f"DEBUG: x-rapidapi-host = {rapidapi_host}")
            logger.info(f"DEBUG: x-forwarded-host = {forwarded_host}")
            
            # Print all headers for complete visibility
            headers_dict = {k: v for k, v in request.headers.items()}
            logger.info(f"DEBUG: All headers: {headers_dict}")
            # ----------------------
            
            # Check host, X-RapidAPI-Host, and X-Forwarded-Host
            if ('zillow-com-live-data-scraper-api.p.rapidapi.com' in host or
                'zillow-com-live-data-scraper-api.p.rapidapi.com' in rapidapi_host or
                'zillow-com-live-data-scraper-api.p.rapidapi.com' in forwarded_host):
                proxy_type = 'PROXIES_LIVE_DATA'
                
        proxy_url = self._load_proxy(proxy_type)
        
        if proxy_type == 'PROXIES_LIVE_DATA' and not proxy_url:
            raise ValueError("PROXIES_LIVE_DATA must be defined when using the zillow-com-live-data-scraper-api host.")
            
        if not proxy_url:
            logger.debug(f"No proxy configured for {proxy_type}. Requests will be made directly.")
            return None
        
        logger.debug(f"Using proxy ({proxy_type}): {proxy_url[:30]}...")
        return {
            'http': proxy_url,
            'https': proxy_url,
        }
    

    
    def get_random_proxy(self) -> Optional[Dict[str, str]]:
        """Alias for get_proxy() for backward compatibility."""
        return self.get_proxy()
    
    def mark_proxy_failed(self, proxy_url: str):
        """No-op: Provider handles rotation, so no need to blacklist."""
        logger.debug(f"Proxy request failed (provider will rotate): {proxy_url[:30]}...")
    
    def mark_proxy_success(self, proxy_url: str):
        """No-op: Provider handles rotation."""
        pass
    
    def get_proxy_count(self) -> int:
        """Return 1 if default proxy configured, 0 otherwise."""
        return 1 if self._load_proxy('PROXIES') else 0
    
    def get_available_proxy_count(self) -> int:
        """Return 1 if proxy configured, 0 otherwise."""
        return self.get_proxy_count()


# Singleton instance
proxy_manager = ProxyManager()
