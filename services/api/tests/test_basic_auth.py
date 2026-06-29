import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.basic_auth import (
    BasicAuthConfig,
    BasicAuthMiddleware,
)


def basic_header(
    username: str,
    password: str,
) -> dict[str, str]:
    token = base64.b64encode(
        f"{username}:{password}".encode()
    ).decode()

    return {"Authorization": f"Basic {token}"}


def build_test_app(
    auth_config: BasicAuthConfig,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        BasicAuthMiddleware,
        auth_config=auth_config,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/private")
    async def private() -> dict[str, str]:
        return {"status": "private"}

    return app


def test_basic_auth_keeps_health_public_and_protects_private_routes() -> None:
    app = build_test_app(
        BasicAuthConfig(
            enabled=True,
            credentials=(("demo-user", "demo-password"),),
        )
    )

    with TestClient(app) as client:
        health_response = client.get("/health")
        unauthorized_response = client.get("/private")
        wrong_response = client.get(
            "/private",
            headers=basic_header(
                "demo-user",
                "wrong-password",
            ),
        )
        authorized_response = client.get(
            "/private",
            headers=basic_header(
                "demo-user",
                "demo-password",
            ),
        )

    assert health_response.status_code == 200
    assert unauthorized_response.status_code == 401
    assert unauthorized_response.headers["www-authenticate"].startswith(
        "Basic "
    )
    assert wrong_response.status_code == 401
    assert authorized_response.status_code == 200


def test_basic_auth_accepts_second_configured_user() -> None:
    app = build_test_app(
        BasicAuthConfig(
            enabled=True,
            credentials=(
                ("first-user", "first-password"),
                ("second-user", "second-password"),
            ),
        )
    )

    with TestClient(app) as client:
        response = client.get(
            "/private",
            headers=basic_header(
                "second-user",
                "second-password",
            ),
        )

    assert response.status_code == 200


def test_disabled_basic_auth_keeps_local_development_open() -> None:
    app = build_test_app(
        BasicAuthConfig(
            enabled=False,
            credentials=(),
        )
    )

    with TestClient(app) as client:
        response = client.get("/private")

    assert response.status_code == 200
