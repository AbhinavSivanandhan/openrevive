from fastapi.testclient import TestClient

from app.main import app


def create_workspace(client: TestClient, name: str = "Research") -> dict:
    response = client.post("/v1/workspaces", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_list_collections_returns_an_empty_json_array() -> None:
    with TestClient(app) as client:
        workspace = create_workspace(client)

        response = client.get(
            f"/v1/workspaces/{workspace['id']}/collections"
        )

    assert response.status_code == 200
    assert response.json() == []


def test_create_collection_returns_created_collection() -> None:
    with TestClient(app) as client:
        workspace = create_workspace(client)

        response = client.post(
            f"/v1/workspaces/{workspace['id']}/collections",
            json={
                "name": "Agent Frameworks",
                "description": "Crawler-first research scope.",
            },
        )

    assert response.status_code == 201

    collection = response.json()
    assert collection["workspace_id"] == workspace["id"]
    assert collection["name"] == "Agent Frameworks"
    assert collection["description"] == "Crawler-first research scope."
    assert collection["id"]
    assert collection["created_at"]
    assert collection["updated_at"]


def test_list_collections_includes_created_collection() -> None:
    with TestClient(app) as client:
        workspace = create_workspace(client)

        create_response = client.post(
            f"/v1/workspaces/{workspace['id']}/collections",
            json={"name": "Agent Frameworks"},
        )
        list_response = client.get(
            f"/v1/workspaces/{workspace['id']}/collections"
        )

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert [collection["name"] for collection in list_response.json()] == [
        "Agent Frameworks"
    ]


def test_collection_routes_return_not_found_for_unknown_workspace() -> None:
    unknown_workspace_id = "00000000-0000-0000-0000-000000000000"

    with TestClient(app) as client:
        response = client.get(
            f"/v1/workspaces/{unknown_workspace_id}/collections"
        )

    assert response.status_code == 404
