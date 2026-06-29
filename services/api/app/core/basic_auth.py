from __future__ import annotations

import base64
import binascii
import secrets
from dataclasses import dataclass

from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app.core.config import Settings

PUBLIC_PATHS = frozenset({"/health", "/health/ready"})
AUTH_REALM = "OpenRevive"


@dataclass(frozen=True, slots=True)
class BasicAuthConfig:
    enabled: bool
    credentials: tuple[tuple[str, str], ...]


def _non_blank(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def build_basic_auth_config(
    settings: Settings,
) -> BasicAuthConfig:
    """
    Build the small allowlist used by the private hackathon deployment.

    Local development stays open unless BASIC_AUTH_ENABLED=true. Production
    fails closed when auth is enabled but credentials are incomplete.
    """
    if not settings.basic_auth_enabled:
        return BasicAuthConfig(
            enabled=False,
            credentials=(),
        )

    username = _non_blank(settings.basic_auth_username)
    password = settings.basic_auth_password

    if username is None or password is None or password == "":
        raise RuntimeError(
            "BASIC_AUTH_ENABLED requires BASIC_AUTH_USERNAME and "
            "BASIC_AUTH_PASSWORD."
        )

    credentials: list[tuple[str, str]] = [(username, password)]

    second_username = _non_blank(settings.basic_auth_username_2)
    second_password = settings.basic_auth_password_2

    has_second_username = second_username is not None
    has_second_password = (
        second_password is not None and second_password != ""
    )

    if has_second_username != has_second_password:
        raise RuntimeError(
            "Set both BASIC_AUTH_USERNAME_2 and BASIC_AUTH_PASSWORD_2, "
            "or neither."
        )

    if second_username is not None and second_password is not None:
        credentials.append((second_username, second_password))

    return BasicAuthConfig(
        enabled=True,
        credentials=tuple(credentials),
    )


def _parse_basic_credentials(
    authorization: str | None,
) -> tuple[str, str] | None:
    if not authorization:
        return None

    scheme, separator, encoded = authorization.partition(" ")

    if scheme.lower() != "basic" or not separator or not encoded:
        return None

    try:
        decoded = base64.b64decode(
            encoded,
            validate=True,
        ).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None

    username, separator, password = decoded.partition(":")

    if not separator:
        return None

    return username, password


def authorization_matches(
    authorization: str | None,
    auth_config: BasicAuthConfig,
) -> bool:
    if not auth_config.enabled:
        return True

    supplied = _parse_basic_credentials(authorization)

    if supplied is None:
        return False

    supplied_username, supplied_password = supplied

    try:
        supplied_username_bytes = supplied_username.encode("utf-8")
        supplied_password_bytes = supplied_password.encode("utf-8")
    except UnicodeEncodeError:
        return False

    matched = False

    for expected_username, expected_password in auth_config.credentials:
        username_matches = secrets.compare_digest(
            supplied_username_bytes,
            expected_username.encode("utf-8"),
        )
        password_matches = secrets.compare_digest(
            supplied_password_bytes,
            expected_password.encode("utf-8"),
        )
        matched = matched or (
            username_matches and password_matches
        )

    return matched


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: object,
        *,
        auth_config: BasicAuthConfig,
    ) -> None:
        super().__init__(app)
        self._auth_config = auth_config

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if (
            not self._auth_config.enabled
            or request.url.path in PUBLIC_PATHS
        ):
            return await call_next(request)

        if authorization_matches(
            request.headers.get("authorization"),
            self._auth_config,
        ):
            return await call_next(request)

        return PlainTextResponse(
            "Authentication required.",
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    f'Basic realm="{AUTH_REALM}", charset="UTF-8"'
                )
            },
        )
