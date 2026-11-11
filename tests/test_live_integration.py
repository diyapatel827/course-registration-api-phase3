import os
import httpx
import pytest

BASE_URL = os.getenv("API_BASE_URL")
pytestmark = pytest.mark.skipif(not BASE_URL, reason="live integration test runs in CI after uvicorn starts")


def test_live_api_health_and_openapi():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/openapi.json").status_code == 200
