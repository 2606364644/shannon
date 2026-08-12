#!/usr/bin/env bash
# HTTPS CONNECT 命门实测：proxy.py resolve_dns 对 HTTPS 代理隧道是否生效。
# 自签 TLS server + curl/chrome 经代理访问 https://target.test -> 应落映射 IP。
# 这是整个方案对真实 HTTPS 目标成立的关键。必须用 bash 跑。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
rm -f /tmp/hits_*.txt /tmp/pA.port /tmp/c.pem /tmp/k.pem /tmp/https_cfg.json
pkill -9 -f playwright-cli 2>/dev/null; pkill -9 -f google-chrome 2>/dev/null
pkill -9 -f tls_server 2>/dev/null; pkill -9 -f host_resolver 2>/dev/null; pkill -9 -f "proxy --plugins" 2>/dev/null
sleep 2

openssl req -x509 -newkey rsa:2048 -keyout /tmp/k.pem -out /tmp/c.pem -days 1 -nodes \
  -subj "/CN=target.test" -addext "subjectAltName=DNS:target.test" >/dev/null 2>&1
echo "自签证书: $([ -s /tmp/c.pem ] && echo OK || echo FAIL)"

PIDS=()
cleanup() {
  playwright-cli kill-all >/dev/null 2>&1
  for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done
  pkill -9 -f tls_server 2>/dev/null; pkill -9 -f host_resolver 2>/dev/null
  pkill -9 -f "proxy --plugins" 2>/dev/null; pkill -9 -f google-chrome 2>/dev/null
}
trap cleanup EXIT

python3 "$HERE/tls_server.py" SERVER-A 127.0.0.1 18443 /tmp/c.pem /tmp/k.pem & PIDS+=("$!")
python3 "$HERE/tls_server.py" SERVER-B 127.0.0.2 18443 /tmp/c.pem /tmp/k.pem & PIDS+=("$!")
HOST_MAP_JSON='{"target.test":"127.0.0.1"}' PYTHONPATH="$HERE" proxy \
  --plugins host_resolver.HostResolverPlugin --hostname 127.0.0.1 --port 0 \
  --port-file /tmp/pA.port --num-workers 1 --num-acceptors 1 --local-executor 1 \
  --log-level WARNING & PIDS+=("$!")
for i in $(seq 1 40); do [ -s /tmp/pA.port ] && break; sleep 0.5; done
PA=$(tr -dc '0-9' < /tmp/pA.port | tail -c6)
echo "proxyA=127.0.0.1:$PA (target.test->127.0.0.1) ; TLS serverA=127.0.0.1:18443"
sleep 1

echo "=== [A] curl HTTPS 经代理（CONNECT 路径）==="
echo "直连 serverA(baseline): $(curl -sk -m 8 https://127.0.0.1:18443/)"
echo "curl -x proxy https://target.test: $(curl -sk -m 15 -x http://127.0.0.1:$PA https://target.test:18443/)"

echo "=== [B] playwright HTTPS 经代理 ==="
python3 -c "
import json
c={'browser':{'browserName':'chromium','launchOptions':{'headless':True,'args':['--no-sandbox','--ignore-certificate-errors'],'proxy':{'server':'http://127.0.0.1:$PA'}},'contextOptions':{'ignoreHTTPSErrors':True}}}
open('/tmp/https_cfg.json','w').write(json.dumps(c))
"
playwright-cli -s=httpA --config /tmp/https_cfg.json open "https://target.test:18443/" 2>&1 | grep -iE "opened|error|fail" | head -2
sleep 2

echo "=== [C] hits 汇总 ==="
echo "hits_SERVER-A=$(wc -l < /tmp/hits_SERVER-A.txt 2>/dev/null || echo 0)"
hA=$(wc -l < /tmp/hits_SERVER-A.txt 2>/dev/null || echo 0)
echo "=== 断言 ==="
[ "$hA" -ge 3 ] && echo "PASS HTTPS CONNECT: resolve_dns 对 HTTPS 隧道生效（curl+playwright 经代理落映射 IP）hits_A=$hA" || echo "结果 hits_A=$hA（直连1+curl1+chrome≥1 => 应≥3）"
echo "=== DONE ==="
