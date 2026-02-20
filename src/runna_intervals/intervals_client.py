"""Intervals.icu REST API client.

Authentication: HTTP Basic Auth where username is the literal string "API_KEY"
and password is your API key from intervals.icu → Settings → Developer Settings.

API docs: https://intervals.icu/api/v1/docs/swagger-ui/index.html
"""

import httpx
from pydantic import SecretStr

from runna_intervals.models.intervals import IntervalsEvent

_BASE_URL = "https://intervals.icu"


class IntervalsAPIError(Exception):
    """Raised when the Intervals.icu API returns an error response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class IntervalsClient:
    """Thin synchronous wrapper around the Intervals.icu REST API."""

    def __init__(
        self,
        api_key: SecretStr,
        athlete_id: str = "i0",
        base_url: str = _BASE_URL,
    ) -> None:
        self._athlete_id = athlete_id
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            auth=("API_KEY", api_key.get_secret_value()),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=30.0,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}/api/v1/athlete/{self._athlete_id}/{path.lstrip('/')}"

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            detail = response.json()
            message = detail.get("message") or detail.get("error") or str(detail)
        except Exception:
            message = response.text or response.reason_phrase

        if response.status_code == 401:
            raise IntervalsAPIError(
                401,
                "Unauthorised — check your API key "
                "(Settings → Developer Settings on intervals.icu).",
            )
        if response.status_code == 403:
            raise IntervalsAPIError(403, "Forbidden — your key may lack the required scope.")
        if response.status_code == 404:
            raise IntervalsAPIError(
                404,
                f"Not found — check your athlete ID (current: '{self._athlete_id}'). {message}",
            )
        raise IntervalsAPIError(response.status_code, message)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_events(
        self,
        events: list[IntervalsEvent],
        upsert: bool = True,
    ) -> list[dict]:  # type: ignore[type-arg]
        """Upload one or more planned workout events.

        Args:
            events: List of events to upload.
            upsert: When True, existing events with the same external_id are updated
                    rather than duplicated (recommended).

        Returns:
            The list of created/updated event objects from the API.
        """
        payload = [
            event.model_dump(exclude_none=True)
            for event in events
        ]
        params = {"upsert": "true"} if upsert else {}
        response = self._client.post(self._url("events/bulk"), json=payload, params=params)
        self._raise_for_status(response)
        return response.json()  # type: ignore[no-any-return]

    def get_events(self, oldest: str, newest: str) -> list[dict]:  # type: ignore[type-arg]
        """Fetch planned events within a date range.

        Args:
            oldest: Start date in YYYY-MM-DD format.
            newest: End date in YYYY-MM-DD format.

        Returns:
            List of event objects from the API.
        """
        response = self._client.get(
            self._url("events"),
            params={"oldest": oldest, "newest": newest},
        )
        self._raise_for_status(response)
        return response.json()  # type: ignore[no-any-return]

    def delete_event(self, event_id: int) -> None:
        """Delete a single planned event by its Intervals.icu integer ID.

        Args:
            event_id: The numeric ID of the event to delete.
        """
        response = self._client.delete(self._url(f"events/{event_id}"))
        self._raise_for_status(response)

    def get_athlete(self) -> dict:  # type: ignore[type-arg]
        """Fetch the authenticated athlete's profile (useful for verifying credentials)."""
        response = self._client.get(
            f"{self._base_url}/api/v1/athlete/{self._athlete_id}"
        )
        self._raise_for_status(response)
        return response.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "IntervalsClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
