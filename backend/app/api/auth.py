"""基于 HttpOnly Cookie 的认证 API。"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.db.models import User
from app.db.session import get_db


router = APIRouter(prefix="/auth", tags=["authentication"])
logger = logging.getLogger(__name__)

ACCESS_COOKIE_NAME = "kanyikan_access"
REFRESH_COOKIE_NAME = "kanyikan_refresh"
ACCESS_MAX_AGE_SECONDS = ACCESS_TOKEN_EXPIRE_MINUTES * 60
REFRESH_MAX_AGE_SECONDS = REFRESH_TOKEN_DAYS * 24 * 60 * 60


def _cookie_secure() -> bool:
    configured = os.getenv("AUTH_COOKIE_SECURE")
    environment = os.getenv("ENV", "development").strip().lower()
    if configured is None:
        return environment not in {"development", "test"}
    secure = configured.strip().lower() in {"1", "true", "yes", "on"}
    if environment in {"production", "prod"} and not secure:
        raise RuntimeError("生产环境必须设置 AUTH_COOKIE_SECURE=true")
    return secure


def _set_session_cookies(
    response: Response,
    *,
    user_id: str,
    session_expires_at: datetime | None = None,
) -> None:
    secure = _cookie_secure()
    now = datetime.now(timezone.utc)
    session_expires_at = session_expires_at or now + timedelta(days=REFRESH_TOKEN_DAYS)
    access_lifetime = min(
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        session_expires_at - now,
    )
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=create_access_token(data={"sub": user_id}, expires_delta=access_lifetime),
        max_age=ACCESS_MAX_AGE_SECONDS,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=create_refresh_token(
            data={"sub": user_id, "session_exp": int(session_expires_at.timestamp())},
            expires_at=session_expires_at,
        ),
        max_age=REFRESH_MAX_AGE_SECONDS,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookies(response: Response) -> None:
    secure = _cookie_secure()
    for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
        response.delete_cookie(
            key=name,
            path="/",
            secure=secure,
            httponly=True,
            samesite="lax",
        )


def _invalid_refresh_response(detail: str) -> JSONResponse:
    logger.warning("auth.refresh.failure")
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": detail},
    )
    _clear_session_cookies(response)
    return response


def _unauthorized(detail: str = "无效的认证会话") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(ACCESS_COOKIE_NAME)
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "access":
        logger.warning("auth.access.final_401")
        raise _unauthorized()

    user_id = payload.get("sub")
    if not user_id:
        logger.warning("auth.access.final_401")
        raise _unauthorized()

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        logger.warning("auth.access.final_401")
        raise _unauthorized("用户不存在或已停用")
    return user


class SessionResponse(BaseModel):
    username: str
    access_expires_in_seconds: int = ACCESS_MAX_AGE_SECONDS
    session_expires_in_seconds: int = REFRESH_MAX_AGE_SECONDS


class UserResponse(BaseModel):
    id: str
    username: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


@router.post("/login", response_model=SessionResponse)
async def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> SessionResponse:
    user = db.query(User).filter(User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        logger.warning("auth.login.failure")
        raise _unauthorized("用户名或密码错误")
    if not user.is_active:
        logger.warning("auth.login.failure")
        raise _unauthorized("账户已停用")

    _set_session_cookies(response, user_id=str(user.id))
    logger.info("auth.login.success")
    return SessionResponse(username=user.username)


@router.post("/refresh", response_model=SessionResponse)
async def refresh_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    token = request.cookies.get(REFRESH_COOKIE_NAME)
    payload = decode_token(token) if token else None
    if payload is None or payload.get("type") != "refresh":
        return _invalid_refresh_response("无效或已过期的刷新会话")

    session_exp = payload.get("session_exp")
    if not isinstance(session_exp, (int, float)):
        return _invalid_refresh_response("刷新会话缺少有效截止时间")
    session_expires_at = datetime.fromtimestamp(session_exp, tz=timezone.utc)
    if session_expires_at <= datetime.now(timezone.utc):
        return _invalid_refresh_response("刷新会话已到最长有效期")

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first() if user_id else None
    if user is None or not user.is_active:
        return _invalid_refresh_response("用户不存在或已停用")

    _set_session_cookies(
        response,
        user_id=str(user.id),
        session_expires_at=session_expires_at,
    )
    logger.info("auth.refresh.success")
    return SessionResponse(username=user.username)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=str(current_user.id),
        username=current_user.username,
        is_active=current_user.is_active,
    )


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    _clear_session_cookies(response)
    logger.info("auth.logout.success")
    return {"message": "登出成功"}
