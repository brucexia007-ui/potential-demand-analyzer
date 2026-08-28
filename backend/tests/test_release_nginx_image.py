"""Windows 离线发行版 Nginx 镜像契约测试。"""
from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH = REPOSITORY_ROOT / "deploy" / "nginx" / "Dockerfile.release"


def test_release_nginx_image_is_digest_pinned_and_self_contained() -> None:
    content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip()]

    assert re.fullmatch(r"FROM nginx@sha256:[0-9a-f]{64}", lines[0])
    assert "COPY nginx.conf /etc/nginx/nginx-http.conf" in lines
    assert (
        "COPY nginx-https.conf.template /etc/nginx/nginx-https.conf.template"
        in lines
    )
    assert "COPY entrypoint.sh /docker-entrypoint.sh" in lines
    assert 'ENTRYPOINT ["/bin/sh", "/docker-entrypoint.sh"]' in lines
    assert "apt-get" not in content
    assert "apk add" not in content
