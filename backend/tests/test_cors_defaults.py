from pathlib import Path


def test_default_cors_allows_only_canonical_loopback_frontend_origin() -> None:
    main_source = (
        Path(__file__).resolve().parents[1] / "main.py"
    ).read_text(encoding="utf-8")

    assert "https://127.0.0.1:10443" in main_source
    assert "http://127.0.0.1:3001" not in main_source
    assert "http://localhost:3001" not in main_source


def test_rate_limiter_uses_cookie_session_not_legacy_bearer() -> None:
    main_source = (
        Path(__file__).resolve().parents[1] / "main.py"
    ).read_text(encoding="utf-8")

    assert 'request.cookies.get("kanyikan_access")' in main_source
    assert 'request.headers.get("Authorization"' not in main_source
