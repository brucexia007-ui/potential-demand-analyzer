def test_legacy_task_websocket_router_is_not_registered():
    from main import app

    paths = {route.path for route in app.routes}
    assert not any(path.startswith("/ws/tasks/") for path in paths)
