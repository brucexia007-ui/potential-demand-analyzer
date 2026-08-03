import pytest

from app.services.provider_semaphore import (
    ProviderConcurrencyLimitError,
    ProviderSemaphore,
)


class FakeRedis:
    def __init__(self):
        self.lease_sets = {}

    def eval(self, script, numkeys, *args):
        if "ZREMRANGEBYSCORE" in script:
            counter_key, _lease_key, limit, token, now, ttl = args
            leases = self.lease_sets.setdefault(counter_key, {})
            for existing_token, expiry in list(leases.items()):
                if expiry <= int(now):
                    del leases[existing_token]
            if len(leases) >= int(limit):
                return 0
            leases[token] = int(now) + int(ttl)
            return 1
        counter_key, token = args
        leases = self.lease_sets.get(counter_key, {})
        return int(leases.pop(token, None) is not None)

    def expire_lease(self, lease_key):
        """模拟进程异常退出后租约自然过期。"""
        token = lease_key.rsplit(":", 1)[-1]
        for leases in self.lease_sets.values():
            if token in leases:
                leases[token] = 0


def test_provider_limit_is_shared_and_release_allows_next_request():
    semaphore = ProviderSemaphore(FakeRedis(), default_limit=1)
    first = semaphore.acquire("DeepSeek")
    assert first is not None
    assert semaphore.acquire("deepseek") is None
    semaphore.release(first)
    assert semaphore.acquire("deepseek") is not None


def test_slot_releases_after_model_failure():
    semaphore = ProviderSemaphore(FakeRedis(), default_limit=1)
    with pytest.raises(RuntimeError):
        with semaphore.slot("provider-a"):
            raise RuntimeError("provider failed")
    with semaphore.slot("provider-a"):
        pass


def test_slot_reports_exhaustion_without_executing_request():
    semaphore = ProviderSemaphore(FakeRedis(), default_limit=1)
    lease = semaphore.acquire("provider-a")
    assert lease is not None
    with pytest.raises(ProviderConcurrencyLimitError):
        with semaphore.slot("provider-a"):
            pass


def test_expired_lease_does_not_permanently_exhaust_provider_capacity():
    client = FakeRedis()
    semaphore = ProviderSemaphore(client, default_limit=1)
    first = semaphore.acquire("deepseek")
    assert first is not None

    client.expire_lease(first.lease_key)

    # Worker 被强制终止后，旧租约会过期；后续调用必须能重新取得容量。
    assert semaphore.acquire("deepseek") is not None
