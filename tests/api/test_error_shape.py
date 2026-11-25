"""Every error leaves the API in the same JSON shape."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_unknown_path_uses_the_error_shape(client: AsyncClient) -> None:
    response = await client.get("/no-such-path")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_unexpected_error_is_hidden_from_the_caller(app: FastAPI) -> None:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail that must stay in the logs")

    # By default the test transport re-raises application errors. Turning that off
    # shows what a real client would receive.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred."}
    }
    assert "secret" not in response.text
