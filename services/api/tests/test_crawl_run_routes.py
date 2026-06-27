from fastapi.testclient import TestClient

from app.main import app


def create_workspace(client: TestClient, name: str = "Crawler Research") -> dict:
    response = client.post("/v1/workspaces", json={"name": name})
    assert response.status_code == 201
    return response.json()


def create_collection(client: TestClient) -> dict:
    workspace = create_workspace(client)

    response = client.post(
        f"/v1/workspaces/{workspace['id']}/collections",
        json={
            "name": "Agent Frameworks",
            "description": "Crawler scope for the demo.",
        },
    )
    assert response.status_code == 201
    return response.json()


def crawl_request_payload() -> dict:
    return {
        "seed_urls": [
            "https://docs.example.com/start",
            "https://docs.example.com/guides",
        ],
        "allowed_domains": [
            "docs.example.com",
        ],
        "max_pages": 25,
        "max_depth": 2,
        "request_timeout_seconds": 15,
        "max_attempts": 3,
    }


def test_start_crawl_run_creates_pending_run_and_seed_jobs() -> None:
    with TestClient(app) as client:
        collection = create_collection(client)

        response = client.post(
            f"/v1/collections/{collection['id']}/crawl-runs",
            headers={"Idempotency-Key": "crawler-demo-run-001"},
            json=crawl_request_payload(),
        )

    assert response.status_code == 201

    crawl_run = response.json()
    assert crawl_run["id"]
    assert crawl_run["collection_id"] == collection["id"]
    assert crawl_run["status"] == "PENDING"
    assert crawl_run["seed_urls"] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guides",
    ]
    assert crawl_run["allowed_domains"] == ["docs.example.com"]
    assert crawl_run["max_pages"] == 25
    assert crawl_run["max_depth"] == 2
    assert crawl_run["request_timeout_seconds"] == 15
    assert crawl_run["max_attempts"] == 3
    assert crawl_run["queued_job_count"] == 2
    assert crawl_run["created_at"]


def test_start_crawl_run_reuses_the_same_run_for_the_same_idempotency_key() -> None:
    with TestClient(app) as client:
        collection = create_collection(client)
        request_url = f"/v1/collections/{collection['id']}/crawl-runs"
        headers = {"Idempotency-Key": "crawler-demo-run-001"}
        payload = crawl_request_payload()

        first_response = client.post(
            request_url,
            headers=headers,
            json=payload,
        )
        second_response = client.post(
            request_url,
            headers=headers,
            json=payload,
        )

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert second_response.json()["id"] == first_response.json()["id"]
    assert second_response.json()["queued_job_count"] == 2


def test_start_crawl_run_rejects_a_seed_outside_allowed_domains() -> None:
    with TestClient(app) as client:
        collection = create_collection(client)

        response = client.post(
            f"/v1/collections/{collection['id']}/crawl-runs",
            headers={"Idempotency-Key": "crawler-demo-run-002"},
            json={
                **crawl_request_payload(),
                "seed_urls": ["https://example.org/not-allowed"],
            },
        )

    assert response.status_code == 422
