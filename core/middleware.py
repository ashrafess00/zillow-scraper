import threading

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
