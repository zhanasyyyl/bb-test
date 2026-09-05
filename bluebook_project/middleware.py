"""
Custom security middleware for the Bluebook project.

Adds headers that Django doesn't provide built-in settings for:
- Content-Security-Policy
- Permissions-Policy
"""

from django.conf import settings


class SecurityHeadersMiddleware:
    """Add Content-Security-Policy and Permissions-Policy headers in production."""

    # CSP directives — intentionally restrictive while allowing the
    # external resources the project actually uses.
    CSP_POLICY = "; ".join([
        "default-src 'self'",
        # Scripts: self + CDN libs + Desmos (lazy-loaded).
        # 'unsafe-inline' needed for the small inline <script> blocks.
        # 'unsafe-eval' needed by MathQuill.
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://ajax.googleapis.com https://cdnjs.cloudflare.com https://www.desmos.com",
        # Styles: self + Google Fonts + inline styles used extensively in templates.
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com",
        # Fonts: self (local woff/woff2 files) + Google Fonts.
        "font-src 'self' https://fonts.gstatic.com",
        # Images: self + data URIs (inline SVG backgrounds).
        "img-src 'self' data:",
        # Connections (fetch/XHR): only same-origin API calls.
        "connect-src 'self'",
        # Disallow embedding in frames, objects, etc.
        "frame-src https://www.desmos.com",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ])

    PERMISSIONS_POLICY = ", ".join([
        "geolocation=()",
        "camera=()",
        "microphone=()",
        "payment=()",
        "usb=()",
    ])

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not settings.DEBUG:
            response["Content-Security-Policy"] = self.CSP_POLICY
            response["Permissions-Policy"] = self.PERMISSIONS_POLICY

        return response
