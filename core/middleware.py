import logging
import secrets
import threading

from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger(__name__)

_thread_locals = threading.local()

def get_current_request():
    """Get the current HTTP request from thread locals."""
    return getattr(_thread_locals, 'request', None)

class RequestMiddleware:
    """
    Middleware that stores the current request in thread-local storage,
    allowing it to be accessed anywhere (e.g., from the proxy manager).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.request = request
        try:
            return self.get_response(request)
        finally:
            if hasattr(_thread_locals, 'request'):
                del _thread_locals.request


class RapidAPIOnlyMiddleware:
    """
    Reject requests that did not come through RapidAPI.

    RapidAPI injects `X-RapidAPI-Proxy-Secret` on the hop between its proxy and
    this server. Subscribers never see the header and cannot forge it, so
    matching it is what stops someone from calling this server's IP directly and
    bypassing RapidAPI billing. Nothing changes for subscribers — they keep
    sending only their own X-RapidAPI-Key.

    Fails open: with RAPIDAPI_PROXY_SECRET unset, nothing is enforced and the
    middleware just reports (once per state, to keep the logs quiet) whether the
    header is arriving. Set the secret only after the logs confirm it is, so a
    wrong value can never lock out a paying subscriber.

    Paths in RAPIDAPI_EXEMPT_PATHS (default: /health) are always allowed, so
    uptime probes and `make health` keep working.
    """

    HEADER = 'HTTP_X_RAPIDAPI_PROXY_SECRET'

    def __init__(self, get_response):
        self.get_response = get_response
        # One-shot log flags for the fail-open observation mode.
        self._seen_with_header = False
        self._seen_without_header = False

    # Read on each request rather than cached at startup, so the secret can be
    # rotated (or overridden in tests) without rebuilding the middleware chain.
    @property
    def secret(self):
        return getattr(settings, 'RAPIDAPI_PROXY_SECRET', '')

    @property
    def exempt_paths(self):
        return tuple(getattr(settings, 'RAPIDAPI_EXEMPT_PATHS', ['/health']))

    def __call__(self, request):
        if request.path in self.exempt_paths:
            return self.get_response(request)

        provided = request.META.get(self.HEADER, '')
        secret = self.secret

        if not secret:
            self._observe(request, provided)
            return self.get_response(request)

        if not provided or not secrets.compare_digest(provided, secret):
            logger.warning(
                "Rejected non-RapidAPI request: path=%s host=%s ip=%s header=%s",
                request.path,
                request.get_host(),
                request.META.get('REMOTE_ADDR'),
                'mismatch' if provided else 'missing',
            )
            # A plain 403 is fine here: these requests did not come from
            # RapidAPI, so they are not counted in the listing's metrics and the
            # HTTP-200 error contract in api/exceptions.py does not apply.
            return JsonResponse(
                {
                    'success': False,
                    'error': 'Forbidden',
                    'message': 'This API is only available through RapidAPI.',
                    'status_code': 403,
                },
                status=403,
            )

        return self.get_response(request)

    def _observe(self, request, provided):
        """Log the first request seen with, and the first seen without, the header."""
        if provided and not self._seen_with_header:
            self._seen_with_header = True
            logger.info(
                "RapidAPI proxy secret header IS being sent (path=%s host=%s). "
                "Safe to set RAPIDAPI_PROXY_SECRET to enable enforcement.",
                request.path, request.get_host(),
            )
        elif not provided and not self._seen_without_header:
            self._seen_without_header = True
            logger.info(
                "Request without the RapidAPI proxy secret header (path=%s host=%s ip=%s). "
                "Enforcement is off (RAPIDAPI_PROXY_SECRET unset), so it was allowed.",
                request.path, request.get_host(), request.META.get('REMOTE_ADDR'),
            )
