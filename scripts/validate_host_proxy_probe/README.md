# HOST per-scan 代理可行性实测探针

验证黑盒扫描「per-scan 本地代理（proxy.py + 自定义 DNS）」方案对所有 HTTP 出口工具的可行性。
对应 spec：`docs/superpowers/specs/2026-08-12-blackbox-host-profile-design.md`。

## 结论（2026-08-12，全通过）

- **per-scan 端口隔离**：每个 scan 起独立 proxy.py 子进程（`--port 0`），独立端口 + 独立映射。
- **resolve_dns HOST 映射**：同域名 `target.test` 经 proxyA→127.0.0.1、proxyB→127.0.0.2，落点正确、互不串。
- **HTTP + HTTPS(CONNECT) 双链路**：`resolve_dns` 对 HTTPS CONNECT 隧道同样生效
  （`curl -x proxy https://target.test` 落映射 IP）——黑盒真实目标（HTTPS）成立。
- **agent-browser**：`--proxy` 原生支持，per-session hits 落点正确。
- **playwright-cli**：`--config browser.launchOptions.proxy` 被接受，per-session 是**独立 browser 进程**（不同 PID）→ 各 proxy 各自生效。

## 依赖

```bash
pip install --break-system-packages proxy.py          # proxy.py 2.4.x（实测 2.4.10）
# agent-browser、playwright-cli、google-chrome 已在扫描环境就位
```

## proxy.py 关键启动 flag（实测必需）

```
--plugins host_resolver.HostResolverPlugin \
--hostname 127.0.0.1 --port 0 --port-file <path> \
--num-workers 1 --num-acceptors 1 --local-executor 1 --log-level WARNING
```

- 必须 `--num-workers 1 --num-acceptors 1 --local-executor 1`，否则按 CPU 核数 fork 多进程。
- 映射经 env `HOST_MAP_JSON='{"host":"ip"}'` 注入（每子进程独立 env → per-scan 隔离）。

## 运行

```bash
# 注意：用 bash 跑，不要用 zsh 内联（后台 & + 数组在 zsh 静默失败）
bash scripts/validate_host_proxy_probe/probe_agent_browser.sh   # 双 session agent-browser
bash scripts/validate_host_proxy_probe/probe_playwright.sh       # 双 session playwright-cli
bash scripts/validate_host_proxy_probe/probe_https_connect.sh    # HTTPS CONNECT 命门
```

每个脚本起两个本地标识 server（127.0.0.1 / 127.0.0.2 同端口）+ 两个 proxy（target.test 各映射一个 IP），
两 session 各走一代理访问同域名，断言各落各 server（看 `hits_SERVER-*.txt` 计数）。

## 文件

- `host_resolver.py` — proxy.py 自定义 DNS 插件（resolve_dns 查 HOST_MAP_JSON）。
- `probe_server.py` — HTTP 标识 server（记 hits）。
- `tls_server.py` — HTTPS 标识 server（自签，记 hits）。
- `probe_agent_browser.sh` / `probe_playwright.sh` / `probe_https_connect.sh` — 三组实测。
