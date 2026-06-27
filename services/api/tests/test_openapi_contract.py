from fastapi.testclient import TestClient

from app.main import app


def test_openapi_schema_exposes_control_plane_routes() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200

    schema = response.json()

    assert schema["info"]["title"] == "OpenRevive API"
    assert {
        "/health",
        "/health/ready",
        "/v1/workspaces",
        "/v1/workspaces/{workspace_id}/collections",
        "/v1/collections/{collection_id}/crawl-runs",
    } <= set(schema["paths"])


def test_swagger_ui_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text
