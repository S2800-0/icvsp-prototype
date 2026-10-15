"""User sign-in and roles (FR-SEC-01, FR-SEC-02, FR-SEC-19, FR-SEC-21, NFR-SEC-02).

The app signs in with Supabase Auth and sends  Authorization: Bearer <access token>  with every request.
This module checks that token (HS256 JWT) and reads the user's role from it.

* The signing secret comes from the environment (SUPABASE_JWT_SECRET), never from the code (NFR-SEC-02).
  With no secret configured, every bearer token is refused (fail closed).
* The role is read from the token's  app_metadata.role  ("driver", "ambulance" or "admin"). Supabase's own
  top-level "role" claim is always "authenticated", and  app_metadata  can only be changed by an admin,
  not by the user (unlike user_metadata).
* The vehicle a user may report for is  app_metadata.vehicle_id  (same reasoning).
* Missing or invalid token -> 401. Valid token but wrong role -> 403.
* Every failure is logged on the "icvsp.security" logger with time and reason only. The token, the password
  and the signature are never logged (FR-SEC-21).
* Ambulance has the same rights as Driver until the security team decides what Ambulance may do extra.
* The old X-Admin-Token header still works on the administrator endpoints (simulation and demo use it).
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

DRIVER, AMBULANCE, ADMIN = "driver", "ambulance", "admin"
ROLES = (DRIVER, AMBULANCE, ADMIN)
_ALIASES = {"administrator": ADMIN}
DEFAULT_AUDIENCE = "authenticated"      # what Supabase puts in "aud" for signed-in users

log = logging.getLogger("icvsp.security")
_bearer = HTTPBearer(auto_error=False, description="Supabase access token")


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    vehicle_id: str | None = None
    via: str = "token"                  # "token" or "admin-token" (legacy header)


class AuthError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _client(request: Request) -> str:
    return request.client.host if request.client else "-"


def decode_token(token: str, secret: str, now: float, audience: str | None = DEFAULT_AUDIENCE) -> dict:
    """Return the claims of a valid HS256 token. Raises AuthError with a short, secret-free reason."""
    if not secret:
        raise AuthError("auth not configured")
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(body_b64))
        sig = _b64(sig_b64)
    except Exception:
        raise AuthError("malformed token") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise AuthError("malformed token")
    if header.get("alg") != "HS256":
        raise AuthError("unsupported algorithm")      # also refuses alg=none
    expected = hmac.new(secret.encode(), f"{head_b64}.{body_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise AuthError("bad signature")
    exp, nbf = claims.get("exp"), claims.get("nbf")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        raise AuthError("missing expiry")
    if now >= exp:
        raise AuthError("expired token")
    if isinstance(nbf, (int, float)) and now < nbf:
        raise AuthError("token not valid yet")
    if audience:
        aud = claims.get("aud")
        if audience not in (aud if isinstance(aud, list) else [aud]):
            raise AuthError("wrong audience")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise AuthError("missing subject")
    return claims


def principal_from_claims(claims: dict) -> Principal:
    meta = claims.get("app_metadata")
    meta = meta if isinstance(meta, dict) else {}
    role = meta.get("role")
    role = _ALIASES.get(role.lower(), role.lower()) if isinstance(role, str) else None
    if role not in ROLES:
        raise AuthError("no valid role in token")
    vehicle = meta.get("vehicle_id")
    return Principal(claims["sub"], role, vehicle if isinstance(vehicle, str) and vehicle else None)


class Auth:
    def __init__(self, secret: str | None = None, admin_token: str | None = None,
                 clock: Callable[[], float] = time.time, audience: str | None = None, limiter=None):
        self.limiter = limiter          # rate limits (ratelimit.py): failed sign-ins per address
        self.secret = secret if secret is not None else os.environ.get("SUPABASE_JWT_SECRET", "")
        self.admin_token = admin_token if admin_token is not None else os.environ.get("ICVSP_ADMIN_TOKEN", "")
        self.audience = audience if audience is not None else os.environ.get("SUPABASE_JWT_AUDIENCE", DEFAULT_AUDIENCE)
        self.clock = clock

    def audit(self, request: Request, reason: str, status: int) -> None:
        """Security event: time, reason, endpoint, caller address. Never the token or any secret."""
        log.warning("auth_failure ts=%.0f status=%d reason=%s method=%s path=%s client=%s",
                    self.clock(), status, reason, request.method, request.url.path,
                    request.client.host if request.client else "-")

    def deny(self, request: Request, status: int, reason: str):
        self.audit(request, reason, status)
        if self.limiter is not None and status in (401, 403):   # count failed sign-ins per address
            self.limiter.hit("auth_fail_ip", _client(request))
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        raise HTTPException(status, reason, headers=headers)

    def require(self, *roles: str, admin_token_ok: bool = False):
        """FastAPI dependency: the caller must hold one of `roles`. With admin_token_ok, a valid legacy
        X-Admin-Token header is accepted as an administrator."""
        allowed = set(roles)

        def dependency(request: Request,
                       creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
            if self.limiter is not None and (wait := self.limiter.blocked("auth_fail_ip", _client(request))):
                self.audit(request, "too many failed sign-ins", 429)
                raise HTTPException(429, "too many failed sign-ins", headers={"Retry-After": str(int(wait) + 1)})
            legacy = request.headers.get("x-admin-token")
            if admin_token_ok and legacy is not None and not (creds and creds.credentials):
                if self.admin_token and hmac.compare_digest(legacy.encode(), self.admin_token.encode()):
                    return Principal("admin-token", ADMIN, None, "admin-token")
                self.deny(request, 401, "invalid admin token")
            if creds is None or not creds.credentials:
                self.deny(request, 401, "missing token")
            try:
                who = principal_from_claims(
                    decode_token(creds.credentials, self.secret, self.clock(), self.audience))
            except AuthError as e:
                self.deny(request, 401 if e.reason != "no valid role in token" else 403, e.reason)
            if who.role not in allowed:
                self.deny(request, 403, "role not allowed")
            return who

        return dependency


def issue_dev_token(secret: str, sub: str, role: str, vehicle_id: str | None = None, exp: float = 4102444800.0) -> str:
    """An HS256 token in Supabase's shape, for tests, the simulation and local demos only. Real tokens come from
    Supabase Auth; the backend never issues tokens itself."""
    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    meta = {"role": role, **({"vehicle_id": vehicle_id} if vehicle_id else {})}
    claims = {"sub": sub, "aud": DEFAULT_AUDIENCE, "role": "authenticated", "app_metadata": meta, "exp": exp}
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"
