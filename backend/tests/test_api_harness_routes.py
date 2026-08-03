def test_legacy_harness_routes_are_not_registered():
    from main import app

    paths = {route.path for route in app.routes}
    assert not any(path.startswith("/api/harness/") for path in paths)
