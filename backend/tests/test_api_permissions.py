"""
权限边界测试 — 401/403 场景覆盖
"""
import pytest
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from jose import jwt
from app.db.auth import SECRET_KEY, ALGORITHM


pytestmark = pytest.mark.asyncio


class TestUnauthenticated:
    """无 token 访问受保护接口 → 401"""

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/tasks"),
        ("POST", "/api/tasks"),
        ("GET", "/api/auth/me"),
        ("GET", "/api/notifications"),
    ])
    async def test_401_no_token(self, unauth_client, method, path):
        if method == "GET":
            response = await unauth_client.get(path)
        else:
            response = await unauth_client.post(path, json={})
        assert response.status_code == 401


class TestFakeToken:
    """伪造/过期 token → 401"""

    async def test_401_fake_signature(self, unauth_client):
        """伪造签名的 JWT"""
        fake = jwt.encode(
            {"sub": "fake", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "type": "access"},
            "wrong-secret", algorithm=ALGORITHM,
        )
        unauth_client.cookies.set("kanyikan_access", fake, domain="test.local", path="/")
        response = await unauth_client.get("/api/auth/me")
        assert response.status_code == 401

    async def test_401_expired_token(self, unauth_client, test_user):
        """过期的 access token"""
        user, _ = test_user
        expired = jwt.encode(
            {"sub": str(user.id), "exp": datetime.now(timezone.utc) - timedelta(minutes=5), "type": "access"},
            SECRET_KEY, algorithm=ALGORITHM,
        )
        unauth_client.cookies.set("kanyikan_access", expired, domain="test.local", path="/")
        response = await unauth_client.get("/api/auth/me")
        assert response.status_code == 401

    async def test_401_garbled_token(self, unauth_client):
        """不是合法 JWT 的字符串"""
        unauth_client.cookies.set("kanyikan_access", "not-a-jwt", domain="test.local", path="/")
        response = await unauth_client.get("/api/auth/me")
        assert response.status_code == 401


class TestCrossUserAccess:
    """跨用户访问资源 → 403"""

    async def test_403_other_user_task_detail(self, auth_client, db_session):
        """访问他人任务详情"""
        from tests.factories import create_test_user, create_test_task
        other_user, _ = create_test_user(db_session)
        task = create_test_task(db_session, other_user.id)
        response = await auth_client.get(f"/api/tasks/{task.id}")
        assert response.status_code == 403

    async def test_403_other_user_report(self, auth_client, db_session):
        """访问他人报告"""
        from tests.factories import create_test_user, create_test_task, create_test_report
        other_user, _ = create_test_user(db_session)
        task = create_test_task(db_session, other_user.id)
        create_test_report(db_session, task.id)
        response = await auth_client.get(f"/api/reports/{task.id}")
        assert response.status_code == 403

    async def test_200_own_task(self, auth_client, test_user, db_session):
        """访问自己的任务 → 200"""
        from tests.factories import create_test_task
        user, _ = test_user
        task = create_test_task(db_session, user.id)
        response = await auth_client.get(f"/api/tasks/{task.id}")
        assert response.status_code == 200
