#!/bin/sh
# ========================================
# Nginx 启动脚本：根据 TLS_ENABLED 选择 HTTP/HTTPS 配置
# ========================================
set -e

CERT_PATH="${TLS_CERT_PATH:-/etc/nginx/certs/fullchain.pem}"
KEY_PATH="${TLS_KEY_PATH:-/etc/nginx/certs/privkey.pem}"
TLS_ENABLED="${TLS_ENABLED:-false}"
ENV="${ENV:-development}"
REDIRECT_SUFFIX="${HTTPS_REDIRECT_PORT_SUFFIX:-}"

if [ -n "$REDIRECT_SUFFIX" ]; then
    if ! echo "$REDIRECT_SUFFIX" | grep -Eq '^:[0-9]{1,5}$' \
        || [ "${REDIRECT_SUFFIX#:}" -gt 65535 ]; then
        echo "ERROR: Invalid HTTPS_REDIRECT_PORT_SUFFIX" >&2
        exit 1
    fi
fi

if [ "$TLS_ENABLED" = "true" ]; then
    if [ ! -f "$CERT_PATH" ] || [ ! -f "$KEY_PATH" ]; then
        echo "ERROR: TLS certificate or private key is missing" >&2
        exit 1
    fi

    echo "==> TLS enabled, using HTTPS config..."
    # 用 sed 替换模板中的占位符
    # 对路径中的 / 进行转义
    ESC_CERT=$(echo "$CERT_PATH" | sed 's/\//\\\//g')
    ESC_KEY=$(echo "$KEY_PATH" | sed 's/\//\\\//g')
    sed "s/\${TLS_CERT_PATH}/${ESC_CERT}/g; s/\${TLS_KEY_PATH}/${ESC_KEY}/g; s/\${HTTPS_REDIRECT_PORT_SUFFIX}/${REDIRECT_SUFFIX}/g" \
        /etc/nginx/nginx-https.conf.template \
        > /etc/nginx/nginx.conf
else
    if [ "$ENV" = "production" ] || [ "$ENV" = "prod" ]; then
        echo "ERROR: Production requires TLS_ENABLED=true" >&2
        exit 1
    fi

    echo "==> TLS not enabled, using HTTP config (default)..."
    cp /etc/nginx/nginx-http.conf /etc/nginx/nginx.conf
fi

echo "==> Starting nginx..."
exec nginx -g 'daemon off;'
