"""Tests for the project endpoints.

The ownership cases matter most here: these are the first routes behind
get_current_user, and the first place one user could see another's data.
"""

import uuid

import pytest
from httpx import AsyncClient

PROJECT = {"name": "sentinelops-api", "repository_url": "https://github.com/acme/sentinelops-api"}


class TestAuthentication:
    async def test_all_routes_require_a_cookie(self, client: AsyncClient) -> None:
        project_id = uuid.uuid4()

        assert (await client.get("/projects")).status_code == 401
        assert (await client.post("/projects", json=PROJECT)).status_code == 401
        assert (await client.get(f"/projects/{project_id}")).status_code == 401
        assert (await client.delete(f"/projects/{project_id}")).status_code == 401


class TestCreate:
    async def test_creates_and_returns_the_project(self, authed_client: AsyncClient) -> None:
        response = await authed_client.post("/projects", json=PROJECT)

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == PROJECT["name"]
        assert body["repository_url"] == PROJECT["repository_url"]

    async def test_response_has_every_contract_field(self, authed_client: AsyncClient) -> None:
        response = await authed_client.post("/projects", json=PROJECT)

        assert set(response.json()) == {
            "id",
            "user_id",
            "name",
            "repository_url",
            "framework",
            "created_at",
            "repository_url_editable",
        }

    async def test_framework_is_null_not_missing(self, authed_client: AsyncClient) -> None:
        """The client's type has no optional keys — it must be present as null."""
        body = (await authed_client.post("/projects", json=PROJECT)).json()

        assert "framework" in body
        assert body["framework"] is None

    async def test_owner_is_the_authenticated_user(self, authed_client: AsyncClient) -> None:
        me = (
            await authed_client.post(
                "/auth/login",
                json={"email": "owner@example.com", "password": "correct horse battery"},
            )
        ).json()

        body = (await authed_client.post("/projects", json=PROJECT)).json()

        assert body["user_id"] == me["id"]

    async def test_cannot_assign_a_project_to_another_user(
        self, authed_client: AsyncClient
    ) -> None:
        """user_id is not an accepted input; supplying one must be ignored."""
        someone_else = str(uuid.uuid4())

        body = (
            await authed_client.post("/projects", json={**PROJECT, "user_id": someone_else})
        ).json()

        assert body["user_id"] != someone_else

    async def test_rejects_a_blank_name(self, authed_client: AsyncClient) -> None:
        response = await authed_client.post("/projects", json={**PROJECT, "name": "   "})

        assert response.status_code == 422

    async def test_trims_surrounding_whitespace_from_the_name(
        self, authed_client: AsyncClient
    ) -> None:
        body = (await authed_client.post("/projects", json={**PROJECT, "name": "  api  "})).json()

        assert body["name"] == "api"

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ssh://git@github.com/acme/repo.git",
            "git@github.com:acme/repo.git",
            "javascript:alert(1)",
            "not-a-url",
        ],
    )
    async def test_rejects_a_non_http_repository_url(
        self, authed_client: AsyncClient, url: str
    ) -> None:
        """A worker will clone this later. file:// would read local paths and the
        rest cannot be handled — cheaper to refuse at the edge."""
        response = await authed_client.post("/projects", json={**PROJECT, "repository_url": url})

        assert response.status_code == 422


class TestList:
    async def test_empty_for_a_new_user(self, authed_client: AsyncClient) -> None:
        response = await authed_client.get("/projects")

        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_newest_first(self, authed_client: AsyncClient) -> None:
        await authed_client.post("/projects", json={**PROJECT, "name": "older"})
        await authed_client.post("/projects", json={**PROJECT, "name": "newer"})

        names = [p["name"] for p in (await authed_client.get("/projects")).json()]

        assert names == ["newer", "older"]

    async def test_excludes_other_users_projects(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        await other_client.post("/projects", json={**PROJECT, "name": "not yours"})
        await authed_client.post("/projects", json={**PROJECT, "name": "yours"})

        names = [p["name"] for p in (await authed_client.get("/projects")).json()]

        assert names == ["yours"]


class TestGet:
    async def test_returns_the_project(self, authed_client: AsyncClient) -> None:
        created = (await authed_client.post("/projects", json=PROJECT)).json()

        response = await authed_client.get(f"/projects/{created['id']}")

        assert response.status_code == 200
        assert response.json() == created

    async def test_unknown_id_is_404(self, authed_client: AsyncClient) -> None:
        response = await authed_client.get(f"/projects/{uuid.uuid4()}")

        assert response.status_code == 404

    async def test_another_users_project_is_404_not_403(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        """403 would confirm the id exists. 401 would be worse — the frontend
        signs the user out on any 401."""
        theirs = (await other_client.post("/projects", json=PROJECT)).json()

        response = await authed_client.get(f"/projects/{theirs['id']}")

        assert response.status_code == 404

    async def test_malformed_id_is_422(self, authed_client: AsyncClient) -> None:
        response = await authed_client.get("/projects/not-a-uuid")

        assert response.status_code == 422


class TestDelete:
    async def test_returns_204_with_no_body(self, authed_client: AsyncClient) -> None:
        """The client special-cases 204 and never calls .json() on it."""
        created = (await authed_client.post("/projects", json=PROJECT)).json()

        response = await authed_client.delete(f"/projects/{created['id']}")

        assert response.status_code == 204
        assert response.content == b""

    async def test_project_is_gone_afterwards(self, authed_client: AsyncClient) -> None:
        created = (await authed_client.post("/projects", json=PROJECT)).json()

        await authed_client.delete(f"/projects/{created['id']}")

        assert (await authed_client.get(f"/projects/{created['id']}")).status_code == 404

    async def test_unknown_id_is_404(self, authed_client: AsyncClient) -> None:
        response = await authed_client.delete(f"/projects/{uuid.uuid4()}")

        assert response.status_code == 404

    async def test_cannot_delete_another_users_project(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        theirs = (await other_client.post("/projects", json=PROJECT)).json()

        response = await authed_client.delete(f"/projects/{theirs['id']}")

        assert response.status_code == 404
        assert (await other_client.get(f"/projects/{theirs['id']}")).status_code == 200
