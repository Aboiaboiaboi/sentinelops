from httpx import AsyncClient

from app.observability import set_metrics_redis


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_ready_reports_database_up(client: AsyncClient) -> None:
    """The client fixture wires a real test Postgres via get_db, so the
    database check should pass. lifespan never runs under ASGITransport
    (see main.py's lifespan docstring), so get_metrics_redis() returns
    None here — redis is correctly reported as not ready, and that alone
    should keep the overall response degraded rather than ok.
    """
    response = await client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"database": True, "redis": False}


async def test_health_ready_reports_ok_when_redis_also_up(client: AsyncClient) -> None:
    """Same request, but with a working Redis pool installed the way
    main.py's lifespan does it for real — proves the 200 path, not just
    the degraded one above.
    """

    class _FakeRedis:
        async def ping(self) -> bool:
            return True

    set_metrics_redis(_FakeRedis())  # type: ignore[arg-type]
    try:
        response = await client.get("/health/ready")
    finally:
        set_metrics_redis(None)  # type: ignore[arg-type]

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": True, "redis": True}
