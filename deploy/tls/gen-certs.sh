#!/usr/bin/env bash
# 瓯医数链联邦 mTLS 证书一键签发（P1.2 节点认证与传输安全）。
#
# 用 openssl CLI 生成私有 CA + 服务端证书 + 节点客户端证书，零第三方依赖。
# 在平台侧运行一次，把客户端证书分发给各医院节点（内网安全通道分发，
# 不要走公网明文传输私钥）。
#
# 用法：
#   ./gen-certs.sh <域名> [节点数] [输出目录]
#   ./gen-certs.sh fed.oumed.cn 2 ./certs
#
# 产出（默认 ./certs/）：
#   ca.key / ca.crt                      联邦根 CA（离线保存，仅用于签发）
#   server.key / server.crt              服务端证书（SAN=域名，nginx 用）
#   client-node1.key/.crt ...            节点客户端证书（分发到节点 Agent）
#   fingerprints.txt                     各证书 SHA-256 指纹（登记留档）
#
# Windows 环境可用 Git Bash 或 WSL 执行。

set -euo pipefail

DOMAIN="${1:?用法: ./gen-certs.sh <域名> [节点数] [输出目录]}"
NODE_COUNT="${2:-2}"
OUT_DIR="${3:-./certs}"
DAYS=825          # 客户端/服务端证书有效期（约 27 个月）
CA_DAYS=3650     # 根 CA 有效期 10 年

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"

echo "==> [1/5] 生成联邦根 CA（${CA_DAYS} 天）"
openssl req -x509 -newkey rsa:3072 -nodes \
    -keyout ca.key -out ca.crt -days "$CA_DAYS" \
    -subj "/CN=oumed-chain-federation-ca/O=瓯医数链" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null

echo "==> [2/5] 生成服务端证书（SAN=${DOMAIN}，${DAYS} 天）"
openssl req -newkey rsa:2048 -nodes \
    -keyout server.key -out server.csr \
    -subj "/CN=${DOMAIN}/O=瓯医数链" 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days "$DAYS" \
    -extfile <(printf "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:%s" "$DOMAIN") 2>/dev/null

echo "==> [3/5] 生成 ${NODE_COUNT} 个节点客户端证书（extendedKeyUsage=clientAuth）"
for i in $(seq 1 "$NODE_COUNT"); do
    openssl req -newkey rsa:2048 -nodes \
        -keyout "client-node${i}.key" -out "client-node${i}.csr" \
        -subj "/CN=fed-node-${i}/O=瓯医数链" 2>/dev/null
    openssl x509 -req -in "client-node${i}.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
        -out "client-node${i}.crt" -days "$DAYS" \
        -extfile <(printf "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=clientAuth") 2>/dev/null
done

echo "==> [4/5] 登记 SHA-256 指纹（供节点注册留档/应用层指纹绑定预留）"
: > fingerprints.txt
for crt in ca.crt server.crt client-node*.crt; do
    fp=$(openssl x509 -in "$crt" -noout -fingerprint -sha256 | cut -d= -f2)
    echo "$crt  $fp" | tee -a fingerprints.txt
done

echo "==> [5/5] 收紧私钥权限 + 清理中间文件"
chmod 600 ./*.key
rm -f ./*.csr ca.srl

echo
echo "完成。分发指引："
echo "  平台 nginx：server.crt/server.key → /etc/nginx/ssl/fullchain.pem、privkey.pem；ca.crt → /etc/nginx/ssl/ca.crt"
echo "  节点 i：client-node\${i}.crt/.key + ca.crt → 节点 Agent 环境变量"
echo "          TLS_CLIENT_CERT / TLS_CLIENT_KEY / TLS_CA_CERT"
echo "吊销与换发：重签对应证书并重启 nginx；CA 泄露 = 全部重签（CA 私钥务必离线保存）"
