"""Redis-backed provider concurrency leases for model calls."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
from uuid import uuid4

import redis


_ACQUIRE = """
local now = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local limit = tonumber(ARGV[1])
if redis.call('ZCARD', KEYS[1]) >= limit then return 0 end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[4]), ARGV[2])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[4]))
return 1
"""
_RELEASE = """
local released = redis.call('ZREM', KEYS[1], ARGV[1])
if redis.call('ZCARD', KEYS[1]) == 0 then redis.call('DEL', KEYS[1]) end
return released
"""


class ProviderSemaphoreUnavailable(RuntimeError):
    pass


class ProviderConcurrencyLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderLease:
    provider: str
    token: str
    counter_key: str
    lease_key: str


class ProviderSemaphore:
    def __init__(
        self,
        client,
        *,
        default_limit: int = 4,
        lease_seconds: int = 300,
        key_prefix: str = "teo:provider-semaphore",
    ) -> None:
        if default_limit < 1 or lease_seconds < 1:
            raise ValueError("default_limit and lease_seconds must be positive")
        self._client = client
        self._default_limit = default_limit
        self._lease_seconds = lease_seconds
        self._key_prefix = key_prefix

    def acquire(self, provider: str, *, limit: int | None = None) -> ProviderLease | None:
        normalized = self._provider(provider)
        effective_limit = self._provider_limit(normalized, limit)
        token = uuid4().hex
        counter_key = f"{self._key_prefix}:{normalized}:leases"
        lease_key = f"{self._key_prefix}:{normalized}:lease:{token}"
        try:
            acquired = self._client.eval(
                _ACQUIRE,
                2,
                counter_key,
                lease_key,
                effective_limit,
                token,
                int(time.time() * 1000),
                self._lease_seconds * 1000,
            )
        except redis.RedisError as error:
            raise ProviderSemaphoreUnavailable("provider semaphore Redis operation failed") from error
        if int(acquired) != 1:
            return None
        return ProviderLease(
            provider=normalized,
            token=token,
            counter_key=counter_key,
            lease_key=lease_key,
        )

    def release(self, lease: ProviderLease) -> None:
        try:
            self._client.eval(_RELEASE, 1, lease.counter_key, lease.token)
        except redis.RedisError as error:
            raise ProviderSemaphoreUnavailable("provider semaphore release failed") from error

    @contextmanager
    def slot(self, provider: str, *, limit: int | None = None) -> Iterator[ProviderLease]:
        lease = self.acquire(provider, limit=limit)
        if lease is None:
            raise ProviderConcurrencyLimitError(f"provider concurrency exhausted: {provider}")
        try:
            yield lease
        finally:
            self.release(lease)

    def _provider_limit(self, provider: str, explicit_limit: int | None) -> int:
        if explicit_limit is not None:
            if explicit_limit < 1:
                raise ValueError("provider limit must be positive")
            return explicit_limit
        value = os.getenv(f"LLM_PROVIDER_{provider.upper()}_MAX_CONCURRENCY")
        if value is None:
            value = os.getenv("LLM_PROVIDER_MAX_CONCURRENCY", str(self._default_limit))
        try:
            parsed = int(value)
        except ValueError as error:
            raise ValueError("provider concurrency must be an integer") from error
        if parsed < 1:
            raise ValueError("provider concurrency must be positive")
        return parsed

    @staticmethod
    def _provider(provider: str) -> str:
        normalized = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in provider.strip().lower())
        if not normalized:
            raise ValueError("provider must not be empty")
        return normalized


_singleton: ProviderSemaphore | None = None


def get_provider_semaphore() -> ProviderSemaphore | None:
    """Return the mandatory distributed semaphore when Redis is configured.

    Local unit tests without REDIS_URL intentionally do not create a network
    client. Deployed services receive REDIS_URL through compose env_file.
    """
    global _singleton
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    if _singleton is None:
        _singleton = ProviderSemaphore(redis.Redis.from_url(redis_url, decode_responses=True))
    return _singleton
