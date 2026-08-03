#!/bin/bash
# ========================================
# 生成自签名 SSL 证书（预发布/内网环境使用）
#
# 用法:
#   bash deploy/generate-self-signed-cert.sh
#
# 注意: 自签名证书会导致浏览器显示不安全警告，
#       仅用于预发布/内网测试环境。
#       生产环境请使用 Let's Encrypt 或购买正式证书。
# ========================================
set -e

CERT_DIR="./deploy/certs"
mkdir -p "$CERT_DIR"

openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$CERT_DIR/privkey.pem" \
    -out "$CERT_DIR/fullchain.pem" \
    -subj "/CN=localhost/O=Kanyikan Staging/C=CN"

chmod 600 "$CERT_DIR/privkey.pem"
chmod 644 "$CERT_DIR/fullchain.pem"

echo ""
echo "自签名证书已生成:"
echo "  证书: $CERT_DIR/fullchain.pem"
echo "  私钥: $CERT_DIR/privkey.pem"
echo ""
echo "下一步: 在 .env 中设置 TLS_ENABLED=true 并重启 nginx"
echo "  docker compose up -d nginx"
