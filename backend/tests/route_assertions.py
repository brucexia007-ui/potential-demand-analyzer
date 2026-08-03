"""路由注册断言在 FastAPI 的延迟 include_router 模型下展开真实路径。"""
from __future__ import annotations

from typing import Any


def registered_route_paths(app: Any) -> set[str]:
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)

        effective_candidates = getattr(route, "effective_candidates", None)
        if not callable(effective_candidates):
            continue
        for candidate in effective_candidates():
            candidate_path = getattr(candidate, "path", None)
            if isinstance(candidate_path, str):
                paths.add(candidate_path)
    return paths
