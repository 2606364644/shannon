#!/usr/bin/env bash
# 双 session per-scan 隔离实测（agent-browser）：
#   scanA via proxyA(target.test->127.0.0.1) -> 命中 serverA
#   scanB via proxyB(target.test->127.0.0.2) -> 命中 serverB
# 落点用 server hits 文件证明。必须用 bash 跑（zsh 内联后台 & 失败）。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
rm -f /tmp/pA.port /tmp/pB.port /tmp/hits_*.txt
export AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/google-chrome
export AGENT_BROWSER_ARGS="--no-sandbox"
AB="agent-browser"

PIDS=()
cleanup() {
  "$AB" close --all >/dev/null 2>&1
  for p in "${PIDS[@]:-}"; do kill -9 "$p" 2>/dev/null; done
  pkill -9 -f host_resolver 2>/dev/null; pkill -9 -f probe_server 2>/dev/null; pkill -9 -f "proxy --plugins" 2>/dev/null
}
trap cleanup EXIT

python3 "$HERE/probe_server.py" SERVER-A 127.0.0.1 18080 & PIDS+=("$!")
python3 "$HERE/probe_server.py" SERVER-B 127.0.0.2 18080 & PIDS+=("$!")
HOST_MAP_JSON='{"target.test":"127.0.0.1"}' PYTHONPATH="$HERE" proxy \
  --plugins host_resolver.HostResolverPlugin --hostname 127.0.0.1 --port 0 \
  --port-file /tmp/pA.port --num-workers 1 --num-acceptors 1 --local-executor 1 \
  --log-level WARNING & PIDS+=("$!")
HOST_MAP_JSON='{"target.test":"127.0.0.2"}' PYTHONPATH="$HERE" proxy \
  --plugins host_resolver.HostResolverPlugin --hostname 127.0.0.1 --port 0 \
  --port-file /tmp/pB.port --num-workers 1 --num-acceptors 1 --local-executor 1 \
  --log-level WARNING & PIDS+=("$!")
for i in $(seq 1 40); do [ -s /tmp/pA.port ] && [ -s /tmp/pB.port ] && break; sleep 0.5; done
PA=$(tr -dc '0-9' < /tmp/pA.port | tail -c6); PB=$(tr -dc '0-9' < /tmp/pB.port | tail -c6)
echo "proxyA=127.0.0.1:$PA  proxyB=127.0.0.1:$PB"
echo "curlA=$(curl -s -m 8 -x http://127.0.0.1:$PA http://target.test:18080/)"
echo "curlB=$(curl -s -m 8 -x http://127.0.0.1:$PB http://target.test:18080/)"

echo "--- scanA via proxyA ---"
"$AB" --session scanA --proxy "http://127.0.0.1:$PA" open "http://target.test:18080/" 2>&1 | grep -E "✓|✗" | head -1
sleep 2
echo "after_scanA hits_A=$(wc -l < /tmp/hits_SERVER-A.txt 2>/dev/null || echo 0) hits_B=$(wc -l < /tmp/hits_SERVER-B.txt 2>/dev/null || echo 0)"
"$AB" close --all >/dev/null 2>&1

echo "--- scanB via proxyB ---"
"$AB" --session scanB --proxy "http://127.0.0.1:$PB" open "http://target.test:18080/" 2>&1 | grep -E "✓|✗" | head -1
sleep 2
echo "after_scanB hits_A=$(wc -l < /tmp/hits_SERVER-A.txt 2>/dev/null || echo 0) hits_B=$(wc -l < /tmp/hits_SERVER-B.txt 2>/dev/null || echo 0)"

hA=$(wc -l < /tmp/hits_SERVER-A.txt 2>/dev/null || echo 0); hB=$(wc -l < /tmp/hits_SERVER-B.txt 2>/dev/null || echo 0)
echo "=== 断言 ==="
[ "$hA" -gt 0 ] && echo "PASS scanA(chrome via proxyA)->serverA(127.0.0.1) hits=$hA" || echo "FAIL scanA"
[ "$hB" -gt 0 ] && echo "PASS scanB(chrome via proxyB)->serverB(127.0.0.2) hits=$hB" || echo "FAIL scanB"
echo "=== DONE ==="
