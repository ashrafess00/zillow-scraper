"""
Scrapers app configuration with session pre-warming at startup.
"""

import threading
import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ScrapersConfig(AppConfig):
    name = 'scrapers'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        """Pre-warm the curl_cffi session pool on server startup."""
        # Run in background thread so Django startup isn't blocked
        thread = threading.Thread(
            target=self._prewarm_session,
            daemon=True,
            name="session-prewarm"
        )
        thread.start()
        logger.info("Session pre-warm thread started")

    def _prewarm_session(self):
        """Background thread that warms up the session pool."""
        try:
            from scrapers.base import session_pool
            # This triggers session creation + zillow.com warmup request
            session_pool.get_session()
            logger.info("Session pre-warm completed successfully")
        except Exception as e:
            logger.warning(f"Session pre-warm failed (will retry on first request): {e}")
