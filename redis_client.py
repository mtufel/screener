"""
Resilient Async Redis Client Wrapper (redis_client.py)

Provides persistent state storage for Upstash Redis / standard Redis with:
1. Dual protocol support: Standard TCP socket (REDIS_URL) or Upstash REST API.
2. Non-blocking error handling: Swallows connection failures and gracefully falls back to local memory/disk.
3. High-level helpers for JSON serialization, 4H FVG cache, and Telegram alert deduplication.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional
import httpx

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

logger = logging.getLogger("redis_client")

REDIS_URL = os.getenv("REDIS_URL", "").strip()
UPSTASH_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
UPSTASH_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "").strip()

APP_ENV = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "local")).strip().lower()
IS_PRODUCTION = APP_ENV in ("production", "prod", "server")
DEFAULT_ENV_TAG = "prod" if IS_PRODUCTION else (APP_ENV or "local")
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", f"screener:{DEFAULT_ENV_TAG}").strip().rstrip(":")


def get_key(sub_key: str, prefix: Optional[str] = None) -> str:
    """Builds an environment-namespaced Redis key."""
    p = prefix if prefix is not None else REDIS_KEY_PREFIX
    clean_sub = sub_key.lstrip(":")
    return f"{p}:{clean_sub}"


class RedisClient:
    """Async wrapper around Redis with resilient error handling and graceful fallback."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        rest_url: Optional[str] = None,
        rest_token: Optional[str] = None,
        key_prefix: Optional[str] = None,
    ):
        self.redis_url = redis_url if redis_url is not None else REDIS_URL
        self.rest_url = rest_url if rest_url is not None else UPSTASH_REST_URL
        self.rest_token = rest_token if rest_token is not None else UPSTASH_REST_TOKEN
        self.key_prefix = key_prefix if key_prefix is not None else REDIS_KEY_PREFIX
        self._redis_conn = None
        self._http_client: Optional[httpx.AsyncClient] = None

    def get_key(self, sub_key: str) -> str:
        """Returns full namespaced key using configured prefix."""
        return get_key(sub_key, prefix=self.key_prefix)

    def is_configured(self) -> bool:
        """Returns True if any Redis connection credentials are configured."""
        return bool(self.redis_url or (self.rest_url and self.rest_token))

    async def _get_conn(self):
        if not self.redis_url or aioredis is None:
            return None
        if self._redis_conn is None:
            try:
                self._redis_conn = aioredis.from_url(
                    self.redis_url,
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=2.0,
                )
            except Exception as exc:
                logger.debug("Failed to initialize Redis socket connection: %s", exc)
                self._redis_conn = None
        return self._redis_conn

    def _get_http(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=3.0)
        return self._http_client

    async def get_str(self, key: str) -> Optional[str]:
        """Fetches a string value by key."""
        if not self.is_configured():
            return None

        # 1. Try socket client
        conn = await self._get_conn()
        if conn:
            try:
                return await conn.get(key)
            except Exception as exc:
                logger.debug("Redis socket GET '%s' failed: %s", key, exc)

        # 2. Try Upstash REST API
        if self.rest_url and self.rest_token:
            try:
                client = self._get_http()
                resp = await client.get(
                    f"{self.rest_url}/get/{key}",
                    headers={"Authorization": f"Bearer {self.rest_token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("result")
            except Exception as exc:
                logger.debug("Upstash REST GET '%s' failed: %s", key, exc)

        return None

    async def set_str(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Sets a string value with optional TTL expiration in seconds."""
        if not self.is_configured():
            return False

        # 1. Try socket client
        conn = await self._get_conn()
        if conn:
            try:
                await conn.set(key, value, ex=ex)
                return True
            except Exception as exc:
                logger.debug("Redis socket SET '%s' failed: %s", key, exc)

        # 2. Try Upstash REST API
        if self.rest_url and self.rest_token:
            try:
                client = self._get_http()
                cmd = ["SET", key, value]
                if ex:
                    cmd.extend(["EX", str(ex)])
                resp = await client.post(
                    self.rest_url,
                    headers={"Authorization": f"Bearer {self.rest_token}"},
                    json=cmd,
                )
                return resp.status_code == 200
            except Exception as exc:
                logger.debug("Upstash REST SET '%s' failed: %s", key, exc)

        return False

    async def get_json(self, key: str) -> Optional[Any]:
        """Fetches and parses a JSON value by key."""
        val = await self.get_str(key)
        if val is None:
            return None
        try:
            return json.loads(val)
        except Exception as exc:
            logger.debug("Failed to decode JSON from Redis key '%s': %s", key, exc)
            return None

    async def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """Serializes and sets a JSON value with optional TTL expiration."""
        try:
            s = json.dumps(value)
            return await self.set_str(key, s, ex=ex)
        except Exception as exc:
            logger.debug("Failed to serialize JSON for Redis key '%s': %s", key, exc)
            return False

    async def exists(self, key: str) -> bool:
        """Checks if a key exists in Redis."""
        if not self.is_configured():
            return False
        conn = await self._get_conn()
        if conn:
            try:
                return bool(await conn.exists(key))
            except Exception as exc:
                logger.debug("Redis socket EXISTS '%s' failed: %s", key, exc)

        if self.rest_url and self.rest_token:
            try:
                client = self._get_http()
                resp = await client.get(
                    f"{self.rest_url}/exists/{key}",
                    headers={"Authorization": f"Bearer {self.rest_token}"},
                )
                if resp.status_code == 200:
                    return resp.json().get("result") == 1
            except Exception as exc:
                logger.debug("Upstash REST EXISTS '%s' failed: %s", key, exc)

        return False

    async def delete(self, key: str) -> bool:
        """Deletes a key from Redis."""
        if not self.is_configured():
            return False
        conn = await self._get_conn()
        if conn:
            try:
                await conn.delete(key)
                return True
            except Exception as exc:
                logger.debug("Redis socket DEL '%s' failed: %s", key, exc)

        if self.rest_url and self.rest_token:
            try:
                client = self._get_http()
                resp = await client.get(
                    f"{self.rest_url}/del/{key}",
                    headers={"Authorization": f"Bearer {self.rest_token}"},
                )
                return resp.status_code == 200
            except Exception as exc:
                logger.debug("Upstash REST DEL '%s' failed: %s", key, exc)

        return False

    # --------------------------------------------------------------------------
    # Higher-Level Application Helpers
    # --------------------------------------------------------------------------
    async def is_alert_sent(self, symbol: str, event_type: str, trade_id: str) -> bool:
        """Checks whether a specific Telegram alert has already been dispatched."""
        key = self.get_key(f"alert:{symbol.upper()}:{event_type}:{trade_id}")
        return await self.exists(key)

    async def mark_alert_sent(
        self, symbol: str, event_type: str, trade_id: str, ttl_seconds: int = 7 * 86400
    ) -> bool:
        """Marks a Telegram alert as sent with a 7-day TTL."""
        key = self.get_key(f"alert:{symbol.upper()}:{event_type}:{trade_id}")
        return await self.set_str(key, "1", ex=ttl_seconds)

    async def close(self):
        """Closes connections."""
        if self._redis_conn:
            try:
                await self._redis_conn.aclose()
            except Exception:
                pass
            self._redis_conn = None
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None


# Global singleton instance
redis_client = RedisClient()
