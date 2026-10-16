"""
SkyTracker Security & Hardening Suite
======================================
Defensive security controls:
- Thread-safe sliding-window rate limiting
- Defense-in-depth HTTP security headers (CSP, HSTS, frame options, nosniff)
- Restrictive CORS origin handling
- Anti-bot honeypot detection
- Cryptographic HMAC deletion tokens (IDOR prevention)
"""

import os
import time
import hmac
import hashlib
import secrets
import threading
from functools import wraps
from collections import defaultdict
from flask import request, jsonify, make_response, current_app


# ============================================================
# CONFIGURATION & SECRETS
# ============================================================
INSECURE_SECRET_KEYS = {
    "replace_with_64_character_cryptographically_secure_random_hex_key",
    "secret",
    "secret_key",
    "changeme",
    "change_me",
    "default",
    "development",
    "dev",
    "test",
    "testing",
    "password",
    "12345678",
}


def _load_secret_key():
    """
    Load and enforce SECRET_KEY.

    Production Behavior:
    - SECRET_KEY MUST be explicitly configured via environment variable.
    - Startup fails immediately if SECRET_KEY is missing, empty, or insecure
      (e.g., placeholder, weak value, or fewer than 32 characters).
    - NEVER generates an ephemeral secret on restart in production to prevent session
      invalidation across restarts and secret divergence across multi-worker Gunicorn processes.

    Local Development / Test Behavior:
    - Uses the configured SECRET_KEY if provided in environment.
    - If absent, generates an ephemeral secret so local development and test suites
      remain convenient without manual configuration.
    """
    env_secret = os.getenv("SECRET_KEY")
    is_production = (
        os.getenv("FLASK_ENV") == "production"
        or bool(os.getenv("RENDER"))
        or os.getenv("ENV") == "production"
        or os.getenv("ENVIRONMENT") == "production"
    )

    if is_production:
        if not env_secret or not env_secret.strip():
            raise RuntimeError("CRITICAL: SECRET_KEY environment variable must be set in production.")

        secret = env_secret.strip()
        lower_secret = secret.lower()
        if (
            lower_secret in INSECURE_SECRET_KEYS
            or lower_secret.startswith("skytracker-dev-entropy-")
            or lower_secret.startswith("dev-")
            or len(secret) < 32
        ):
            raise RuntimeError(
                "CRITICAL: SECRET_KEY environment variable is insecure. "
                "A minimum 32-character (recommended 64-character hex) random key is required in production."
            )
        return secret

    # Local development / testing:
    if env_secret and env_secret.strip():
        return env_secret.strip()
    return f"skytracker-dev-entropy-{secrets.token_hex(24)}"


SECRET_KEY = _load_secret_key()
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]


# ============================================================
# 1. THREAD-SAFE SLIDING WINDOW RATE LIMITER
# ============================================================
class SlidingWindowRateLimiter:
    """Thread-safe in-memory sliding window rate limiter."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests = defaultdict(list)

    def is_allowed(self, key: str, max_requests: int, window_seconds: int):
        """
        Check if request is permitted under rate limit.
        Returns: (allowed: bool, remaining: int, retry_after: int)
        """
        now = time.time()
        window_start = now - window_seconds

        with self._lock:
            # Clean timestamps older than current window
            timestamps = self._requests[key]
            self._requests[key] = [t for t in timestamps if t > window_start]
            valid_requests = self._requests[key]

            if len(valid_requests) >= max_requests:
                earliest = valid_requests[0]
                retry_after = max(1, int(earliest + window_seconds - now))
                return False, 0, retry_after

            # Record this request
            self._requests[key].append(now)
            remaining = max_requests - len(self._requests[key])
            return True, remaining, 0

    def clear(self):
        """Reset all rate limiter states (useful for tests)."""
        with self._lock:
            self._requests.clear()


limiter = SlidingWindowRateLimiter()


def get_client_ip():
    """
    Extract real client IP safely.
    Relies on request.remote_addr which is sanitized by ProxyFix behind reverse proxies,
    preventing client-spoofed X-Forwarded-For headers from bypassing rate limits.
    """
    return (request.remote_addr or "127.0.0.1").strip()


def rate_limit(limit: int = 60, window: int = 60, methods: tuple = None):
    """
    Decorator to apply sliding-window rate limiting on Flask endpoints.
    Bypasses throttling in test mode (app.config['TESTING']) unless testing rate limits (app.config['TESTING_RATE_LIMITS']).
    If methods is specified (e.g. methods=('POST',)), only requests matching those HTTP
    verbs are throttled, preventing GET requests (e.g. form views) from being blocked.

    Architectural & Operational Notes:
    - In-Memory Multi-Worker Limitation: This limiter utilizes an in-memory sliding window
      per OS worker process. When deployed on multi-worker application servers (such as Gunicorn
      with N workers), each worker process maintains an isolated in-memory requests dictionary.
      Requests distributed across workers may be throttled independently, effectively multiplying
      the allowed rate threshold by the active worker count. For strict cluster-wide rate limiting,
      a shared, centralized datastore (e.g., Redis) should be introduced.
    - Shared-IP / Corporate NAT Considerations: Clients situated behind shared egress gateways
      or enterprise NATs share the same client IP address. To prevent denial-of-service to legitimate
      users on shared networks, rate limits should be scoped specifically to sensitive action verbs
      (POST) rather than blocking idempotent page views (GET).
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Allow tests to run unimpeded unless specifically verifying rate limits
            if current_app.config.get("TESTING") and not current_app.config.get("TESTING_RATE_LIMITS"):
                return f(*args, **kwargs)

            # If specific HTTP methods are targeted, skip throttling for other methods
            if methods and request.method not in methods:
                return f(*args, **kwargs)

            ip = get_client_ip()
            key = f"{request.method}:{request.endpoint or request.path}:{ip}"

            allowed, remaining, retry_after = limiter.is_allowed(
                key=key,
                max_requests=limit,
                window_seconds=window
            )

            if not allowed:
                response = jsonify({
                    "error": "Too many requests. Please slow down and try again.",
                    "retry_after": retry_after
                })
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                response.headers["X-RateLimit-Limit"] = str(limit)
                response.headers["X-RateLimit-Remaining"] = "0"
                return response

            res = make_response(f(*args, **kwargs))
            res.headers["X-RateLimit-Limit"] = str(limit)
            res.headers["X-RateLimit-Remaining"] = str(remaining)
            return res
        return decorated_function
    return decorator



# ============================================================
# 2. CORS & HTTP SECURITY HEADERS
# ============================================================
def apply_security_headers(response):
    """Apply defense-in-depth HTTP security headers to all responses."""
    # MIME sniffing protection
    response.headers["X-Content-Type-Options"] = "nosniff"

    # Clickjacking protection
    response.headers["X-Frame-Options"] = "SAMEORIGIN"

    # Referrer policy
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # Restrictive browser permissions
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"

    # Content Security Policy (strict yet supports Three.js, fonts, and inline scripts)
    csp_directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        "img-src 'self' data: blob: https:",
        "connect-src 'self'",
        "frame-ancestors 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'"
    ]
    response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

    # Enforce HSTS when running on HTTPS
    is_https = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    if is_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

    # Restrictive CORS Handling
    origin = request.headers.get("Origin")
    if origin:
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Delete-Token"
        # If not allowed, do NOT set wildcard or credentials

    return response


# ============================================================
# 3. IDOR MITIGATION: CRYPTOGRAPHIC DELETION TOKENS
# ============================================================
def generate_delete_token(tracked_id: int, created_at: str) -> str:
    """Generate a HMAC-SHA256 signature for authorized deletion of a tracked flight."""
    payload = f"{tracked_id}:{created_at}".encode("utf-8")
    return hmac.new(SECRET_KEY.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_delete_token(tracked_id: int, created_at: str, token: str) -> bool:
    """Verify that a provided delete token matches the expected cryptographic HMAC."""
    if not token or not isinstance(token, str):
        return False
    expected = generate_delete_token(tracked_id, created_at)
    return hmac.compare_digest(expected, token.strip())


# ============================================================
# 4. BOT HONEYPOT PROTECTION
# ============================================================
HONEYPOT_FIELDS = ("website", "company_url", "fax_number", "phone_trap")


def check_bot_honeypot(data: dict):
    """
    Check for automated spam bots filling hidden honeypot fields.
    Raises ValueError if automated submission is detected.
    """
    if not isinstance(data, dict):
        return
    for field in HONEYPOT_FIELDS:
        val = data.get(field)
        if val and str(val).strip():
            raise ValueError("Automated submission detected.")
