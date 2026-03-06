"""
Async HTTP client for Dialpad API v2.
Handles transcript fetching with retry logic.
"""
import json
import logging
import httpx
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class DialpadClient:
    """Async client for Dialpad API v2."""

    def __init__(self):
        self.base_url = settings.dialpad_api_base_url.rstrip("/")
        self.api_key = settings.dialpad_api_key
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_transcript(self, call_id: str) -> Optional[dict]:
        """
        Fetch AI transcript for a call.
        GET /transcripts/{call_id}

        Returns dict with 'moments' and 'summary', or None if not available.
        """
        client = await self._get_client()
        try:
            response = await client.get(f"/transcripts/{call_id}")

            if response.status_code == 200:
                data = response.json()
                # Log the actual response structure for debugging
                keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
                moments_count = len(data.get("moments", [])) if isinstance(data, dict) else 0
                raw_preview = json.dumps(data)[:500]
                logger.info(
                    f"Transcript API response for call {call_id}: "
                    f"keys={keys}, moments={moments_count}, "
                    f"has_summary={bool(data.get('summary') if isinstance(data, dict) else False)}, "
                    f"preview={raw_preview}"
                )
                return data
            elif response.status_code == 404:
                logger.info(f"No transcript available for call {call_id} (404)")
                return None
            elif response.status_code == 429:
                logger.warning(f"Rate limited fetching transcript for call {call_id}")
                return None
            else:
                logger.error(
                    f"Failed to fetch transcript for call {call_id}: "
                    f"{response.status_code} {response.text[:300]}"
                )
                return None

        except httpx.RequestError as e:
            logger.error(f"Network error fetching transcript for call {call_id}: {e}")
            return None

    async def get_call(self, call_id: str) -> Optional[dict]:
        """
        Get call details.
        GET /call/{call_id}

        Note: 10/min rate limit — use sparingly.
        """
        client = await self._get_client()
        try:
            response = await client.get(f"/call/{call_id}")
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    f"Failed to fetch call {call_id}: "
                    f"{response.status_code} {response.text}"
                )
                return None
        except httpx.RequestError as e:
            logger.error(f"Network error fetching call {call_id}: {e}")
            return None


# Singleton instance
dialpad_client = DialpadClient()
