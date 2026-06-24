from fastapi.testclient import TestClient

from app.main import app


def test_list_workspaces_returns_an_empty_json_array() -> None:
    with TestClient(app) as client:
        response = client.get("/v1/workspaces")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == []


def test_create_workspace_returns_created_workspace() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/workspaces",
            json={"name": "Research"},
        )

    assert response.status_code == 201

    workspace = response.json()
    assert workspace["name"] == "Research"
    assert workspace["id"]
    assert workspace["created_at"]
    assert workspace["updated_at"]


def test_list_workspaces_includes_created_workspace() -> None:
    with TestClient(app) as client:
        create_response = client.post(
            "/v1/workspaces",
            json={"name": "Research"},
        )
        list_response = client.get("/v1/workspaces")

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert [workspace["name"] for workspace in list_response.json()] == [
        "Research"
    ]
