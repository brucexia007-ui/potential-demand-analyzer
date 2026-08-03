"""HttpOnly Cookie authentication contract tests."""

from datetime import datetime, timedelta, timezone

import jwt

from app.db.auth import ALGORITHM, SECRET_KEY


ACCESS_COOKIE = "kanyikan_access"
REFRESH_COOKIE = "kanyikan_refresh"


def _refresh_token(
    user_id: str,
    *,
    expires_delta: timedelta = timedelta(days=1),
    session_expires_at: datetime | None = None,
) -> str:
    session_expires_at = session_expires_at or datetime.now(timezone.utc) + expires_delta
    return jwt.encode(
        {
            "sub": user_id,
            "exp": datetime.now(timezone.utc) + expires_delta,
            "type": "refresh",
            "session_exp": int(session_expires_at.timestamp()),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


class TestLogin:
    async def test_login_sets_http_only_cookie_session(self, unauth_client, test_user):
        user, password = test_user
        response = await unauth_client.post(
            "/api/auth/login",
            data={"username": user.username, "password": password},
        )

        assert response.status_code == 200
        assert response.json() == {
            "username": user.username,
            "access_expires_in_seconds": 1800,
            "session_expires_in_seconds": 604800,
        }
        assert "access_token" not in response.text
        assert "refresh_token" not in response.text

        cookies = response.headers.get_list("set-cookie")
        access = next(value for value in cookies if value.startswith(f"{ACCESS_COOKIE}="))
        refresh = next(value for value in cookies if value.startswith(f"{REFRESH_COOKIE}="))
        for value in (access, refresh):
            lowered = value.lower()
            assert "httponly" in lowered
            assert "samesite=lax" in lowered
            assert "path=/" in lowered
        assert "max-age=1800" in access.lower()
        assert "max-age=604800" in refresh.lower()

        me = await unauth_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["id"] == str(user.id)

    async def test_login_wrong_password(self, unauth_client, test_user):
        user, _ = test_user
        response = await unauth_client.post(
            "/api/auth/login",
            data={"username": user.username, "password": "wrong_password"},
        )
        assert response.status_code == 401


class TestRefresh:
    async def test_refresh_rotates_cookie_session(self, unauth_client, test_user):
        user, _ = test_user
        session_expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        previous = _refresh_token(str(user.id), session_expires_at=session_expires_at)
        unauth_client.cookies.set(REFRESH_COOKIE, previous, domain="test.local", path="/")

        response = await unauth_client.post("/api/auth/refresh")

        assert response.status_code == 200
        assert "access_token" not in response.text
        assert unauth_client.cookies.get(ACCESS_COOKIE)
        rotated = unauth_client.cookies.get(REFRESH_COOKIE)
        assert rotated
        payload = jwt.decode(rotated, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["session_exp"] == int(session_expires_at.timestamp())

    async def test_refresh_rejects_request_body_legacy_token(self, unauth_client, test_user):
        user, _ = test_user
        response = await unauth_client.post(
            "/api/auth/refresh",
            json={"refresh_token": _refresh_token(str(user.id))},
        )
        assert response.status_code == 401

    async def test_refresh_rejects_expired_cookie(self, unauth_client, test_user):
        user, _ = test_user
        unauth_client.cookies.set(
            REFRESH_COOKIE,
            _refresh_token(str(user.id), expires_delta=timedelta(seconds=-1)),
            domain="test.local",
            path="/",
        )
        response = await unauth_client.post("/api/auth/refresh")
        assert response.status_code == 401
        cookies = response.headers.get_list("set-cookie")
        assert any(value.startswith(f"{ACCESS_COOKIE}=") and "Max-Age=0" in value for value in cookies)
        assert any(value.startswith(f"{REFRESH_COOKIE}=") and "Max-Age=0" in value for value in cookies)


class TestCurrentUser:
    async def test_me_rejects_authorization_bearer(self, unauth_client, test_user, token_factory):
        user, _ = test_user
        response = await unauth_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token_factory(str(user.id))}"},
        )
        assert response.status_code == 401

    async def test_me_accepts_access_cookie(self, unauth_client, test_user, token_factory):
        user, _ = test_user
        unauth_client.cookies.set(
            ACCESS_COOKIE,
            token_factory(str(user.id)),
            domain="test.local",
            path="/",
        )
        response = await unauth_client.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json() == {
            "id": str(user.id),
            "username": user.username,
            "is_active": True,
        }


class TestLogout:
    async def test_logout_clears_both_cookies(self, unauth_client, test_user, token_factory):
        user, _ = test_user
        unauth_client.cookies.set(
            ACCESS_COOKIE,
            token_factory(str(user.id)),
            domain="test.local",
            path="/",
        )
        unauth_client.cookies.set(
            REFRESH_COOKIE,
            _refresh_token(str(user.id)),
            domain="test.local",
            path="/",
        )

        response = await unauth_client.post("/api/auth/logout")

        assert response.status_code == 200
        cookies = response.headers.get_list("set-cookie")
        assert any(value.startswith(f"{ACCESS_COOKIE}=") and "Max-Age=0" in value for value in cookies)
        assert any(value.startswith(f"{REFRESH_COOKIE}=") and "Max-Age=0" in value for value in cookies)
        assert (await unauth_client.get("/api/auth/me")).status_code == 401
