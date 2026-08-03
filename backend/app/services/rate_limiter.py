"""Redis Token Bucket 限流器"""
import time
import os
import logging
import redis

logger = logging.getLogger(__name__)


class TokenBucket:
    """基于 Redis 的令牌桶限流器"""

    def __init__(self, redis_url: str | None = None):
        redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
        try:
            self._client = redis.Redis.from_url(redis_url, decode_responses=True)
            self._client.ping()
        except redis.RedisError:
            logger.warning("Redis 不可用，限流器降级为空操作")
            self._client = None

    def allow(self, key: str, max_tokens: int = 60, refill_rate: float = 10.0) -> bool:
        """
        检查是否允许请求。

        Args:
            key: 限流键（如 user:{id}、llm_api、search_api）
            max_tokens: 桶容量（最大令牌数）
            refill_rate: 令牌补充速率（个/秒）

        Returns:
            True 允许，False 触发限流
        """
        if not self._client:
            return True  # Redis 不可用时降级放行

        now = time.time()
        bucket_key = f"rate_limit:{key}"
        last_key = f"rate_limit:{key}:last"

        try:
            pipe = self._client.pipeline()
            pipe.get(bucket_key)
            pipe.get(last_key)
            tokens_raw, last_raw = pipe.execute()

            tokens = float(tokens_raw) if tokens_raw else float(max_tokens)
            last_time = float(last_raw) if last_raw else now

            # 补充令牌
            elapsed = now - last_time
            refill = elapsed * refill_rate
            tokens = min(float(max_tokens), tokens + refill)

            if tokens < 1.0:
                return False

            # 消费一个令牌
            tokens -= 1.0
            pipe = self._client.pipeline()
            pipe.set(bucket_key, tokens, ex=3600)
            pipe.set(last_key, now, ex=3600)
            pipe.execute()
            return True
        except redis.RedisError:
            return True  # 异常时降级放行

    def remaining(self, key: str, max_tokens: int = 60, refill_rate: float = 10.0) -> int:
        """查询剩余可用请求数"""
        if not self._client:
            return max_tokens
        try:
            tokens_raw = self._client.get(f"rate_limit:{key}")
            tokens = float(tokens_raw) if tokens_raw else float(max_tokens)
            # 简化为当前 tokens 取整
            last_raw = self._client.get(f"rate_limit:{key}:last")
            if last_raw:
                elapsed = time.time() - float(last_raw)
                tokens = min(float(max_tokens), tokens + elapsed * refill_rate)
            return max(0, int(tokens))
        except redis.RedisError:
            return max_tokens


# 全局单例
_limiter: TokenBucket | None = None


def get_rate_limiter() -> TokenBucket:
    global _limiter
    if _limiter is None:
        _limiter = TokenBucket()
    return _limiter
