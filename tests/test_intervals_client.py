"""Tests for the Intervals.icu API client."""

import json

import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from runna_intervals.intervals_client import IntervalsAPIError, IntervalsClient
from runna_intervals.models.intervals import IntervalsEvent


BASE = "https://intervals.icu"
ATHLETE = "i99"
API_KEY = SecretStr("test-key")

EVENT = IntervalsEvent(
    start_date_local="2026-03-01T00:00:00",
    name="Easy Run",
    description="- 5km easy",
    moving_time=1800,
    external_id="runna-abc123",
)


@pytest.fixture()
def client() -> IntervalsClient:
    return IntervalsClient(api_key=API_KEY, athlete_id=ATHLETE, base_url=BASE)


# ---------------------------------------------------------------------------
# upload_events
# ---------------------------------------------------------------------------


class TestUploadEvents:
    def test_success(self, client: IntervalsClient, httpx_mock: HTTPXMock) -> None:
        returned = [{"id": 1, "name": "Easy Run"}]
        httpx_mock.add_response(
            method="POST",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events/bulk?upsert=true",
            json=returned,
        )
        result = client.upload_events([EVENT])
        assert result == returned

    def test_upsert_false_omits_param(
        self, client: IntervalsClient, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="POST",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events/bulk",
            json=[],
        )
        result = client.upload_events([EVENT], upsert=False)
        assert result == []

    def test_sends_external_id(
        self, client: IntervalsClient, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="POST",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events/bulk?upsert=true",
            json=[{"id": 1}],
        )
        client.upload_events([EVENT])
        request = httpx_mock.get_request()
        body = json.loads(request.content)  # type: ignore[union-attr]
        assert body[0]["external_id"] == "runna-abc123"

    def test_raises_on_401(self, client: IntervalsClient, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="POST",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events/bulk?upsert=true",
            status_code=401,
            json={"message": "Unauthorized"},
        )
        with pytest.raises(IntervalsAPIError) as exc_info:
            client.upload_events([EVENT])
        assert exc_info.value.status_code == 401
        assert "API key" in str(exc_info.value)


# ---------------------------------------------------------------------------
# get_events
# ---------------------------------------------------------------------------


class TestGetEvents:
    def test_success(self, client: IntervalsClient, httpx_mock: HTTPXMock) -> None:
        events = [{"id": 10, "name": "Threshold Run", "start_date_local": "2026-04-01"}]
        httpx_mock.add_response(
            method="GET",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events?oldest=2026-04-01&newest=2026-04-30",
            json=events,
        )
        result = client.get_events("2026-04-01", "2026-04-30")
        assert result == events

    def test_empty_range(self, client: IntervalsClient, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events?oldest=2026-01-01&newest=2026-01-01",
            json=[],
        )
        assert client.get_events("2026-01-01", "2026-01-01") == []

    def test_raises_on_404(self, client: IntervalsClient, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events?oldest=2026-01-01&newest=2026-01-31",
            status_code=404,
            json={"message": "athlete not found"},
        )
        with pytest.raises(IntervalsAPIError) as exc_info:
            client.get_events("2026-01-01", "2026-01-31")
        assert exc_info.value.status_code == 404
        assert ATHLETE in str(exc_info.value)


# ---------------------------------------------------------------------------
# delete_event
# ---------------------------------------------------------------------------


class TestDeleteEvent:
    def test_success(self, client: IntervalsClient, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="DELETE",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events/42",
            status_code=200,
            json={},
        )
        client.delete_event(42)  # should not raise

    def test_raises_on_403(self, client: IntervalsClient, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="DELETE",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events/99",
            status_code=403,
            json={"message": "Forbidden"},
        )
        with pytest.raises(IntervalsAPIError) as exc_info:
            client.delete_event(99)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# get_athlete
# ---------------------------------------------------------------------------


class TestGetAthlete:
    def test_success(self, client: IntervalsClient, httpx_mock: HTTPXMock) -> None:
        profile = {"id": ATHLETE, "name": "Test Athlete"}
        httpx_mock.add_response(
            method="GET",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}",
            json=profile,
        )
        result = client.get_athlete()
        assert result == profile

    def test_only_one_request_made(
        self, client: IntervalsClient, httpx_mock: HTTPXMock
    ) -> None:
        """Regression: get_athlete must make exactly one HTTP request."""
        httpx_mock.add_response(
            method="GET",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}",
            json={"id": ATHLETE},
        )
        client.get_athlete()
        assert len(httpx_mock.get_requests()) == 1


# ---------------------------------------------------------------------------
# _raise_for_status — generic error branch
# ---------------------------------------------------------------------------


class TestRaiseForStatus:
    def test_generic_error_with_json_message(
        self, client: IntervalsClient, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events?oldest=x&newest=y",
            status_code=422,
            json={"message": "Invalid date format"},
        )
        with pytest.raises(IntervalsAPIError) as exc_info:
            client.get_events("x", "y")
        assert exc_info.value.status_code == 422
        assert "Invalid date format" in str(exc_info.value)

    def test_generic_error_with_plain_text(
        self, client: IntervalsClient, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}/events?oldest=x&newest=y",
            status_code=500,
            text="Internal Server Error",
        )
        with pytest.raises(IntervalsAPIError) as exc_info:
            client.get_events("x", "y")
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_can_be_used_as_context_manager(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            method="GET",
            url=f"{BASE}/api/v1/athlete/{ATHLETE}",
            json={"id": ATHLETE},
        )
        with IntervalsClient(api_key=API_KEY, athlete_id=ATHLETE, base_url=BASE) as c:
            result = c.get_athlete()
        assert result["id"] == ATHLETE
