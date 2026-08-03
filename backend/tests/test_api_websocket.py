from tests.route_assertions import registered_route_paths


def test_legacy_task_websocket_router_is_not_registered():
    from main import app

    paths = registered_route_paths(app)
    assert not any(path.startswith("/ws/tasks/") for path in paths)
