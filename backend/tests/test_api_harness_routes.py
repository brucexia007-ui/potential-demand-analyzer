from tests.route_assertions import registered_route_paths


def test_legacy_harness_routes_are_not_registered():
    from main import app

    paths = registered_route_paths(app)
    assert not any(path.startswith("/api/harness/") for path in paths)
