# HOST 档案

HOST 档案保存“域名 → IPv4”映射，用于让内网/自定义 DNS 环境中的目标可解析，并保证一次扫描内 HTTP、HTTPS CONNECT、浏览器和 LLM 工具出口使用同一份快照。

## 数据模型

`HostProfile`：

- `id`
- `name`
- `source_url`：可选 `/etc/hosts` 格式来源，可刷新
- `mappings[]`
- `scope`: `workspace` / `system`

`HostMapping`：

- `ip`
- `host`

校验规则：

- 仅支持 IPv4；IPv6、loopback、link-local、unspecified 地址拒绝。
- host 必须是 bare hostname：不含协议、端口、path、通配符、`@`、空白；标签符合 DNS 形态。
- host 统一 strip + lowercase，与 `urlparse(url).hostname` 的查询口径一致。
- 同一 profile 内一个 host 不能映射到不同 IP，避免运行时选择不确定。

IP 与域名不按敏感信息处理，档案明文落盘；密码/TOTP 等仍属于认证档案加密体系。

## 作用域与存储

```text
workspaces/<ws>/host-profiles.yaml
workspaces/.system/host-profiles.yaml
```

- system 档案由 configs seed，所有 ws 共享、只读。
- workspace 档案可编辑。
- fork 系统档案时保留同 id，workspace 副本优先遮蔽系统原型。
- 读取时 workspace 段按 id 覆盖 `.system` 段，避免重复显示。
- workspace segment 和路径边界检查防止路径穿越。

## `/etc/hosts` 导入与刷新

`parse_etc_hosts(text)` 支持：

- `#` 注释
- 一行多个 hostname
- Tab/多空格分隔
- `1.2.3.4 hostname` 格式

导入/刷新 URL 时执行 SSRF 防护：

- 必须是绝对 http(s) URL；
- 对来源 URL 本身解析出的所有地址执行 ANY→block 检查，阻止 loopback、unspecified、link-local 等敏感目标；私人内网地址可以是合法 HOST 映射，不在此层一刀切；
- 禁止跟随重定向，防止 302 跳到云元数据/内网地址；
- 来源响应最大 1 MiB；
- 解析出的每条 mapping 仍要过模型校验；
- 空结果拒绝；
- 非法行保留 warning，不静默虚构映射。

刷新语义：

- 有 `source_url` 时重新拉取。
- 普通网络/解析失败保留旧快照并记录 warning，不阻断扫描。
- 刷新结果为空时抛出明确错误，不把空档案当成功。
- system 档案不能直接 refresh；必须 fork 后刷新副本，避免把系统原型写成 workspace 副本。

## API

路由位于 `/api/workspaces/{ws}/host-profiles`：

- 列表/详情：workspace member。
- 创建、更新、删除、导入、fork、刷新：workspace manager。
- `POST /parse`：拉取并解析 URL，只返回 mappings/warnings 预览，不落盘；前端确认后可用普通 `POST` 创建档案。
- `POST /{pid}/refresh`：按 source_url 更新快照。
- system 档案修改/删除/刷新均拒绝，fork 允许。

## 扫描时快照与 per-scan proxy

Web 提交扫描时解析两种互斥来源：

- `host_profile_id`
- `host_url`

解析结果是不可变 snapshot，包含 source、profile id、source_url、mappings、warnings、resolved_at。带 `source_url` 的 profile 会先 best-effort refresh；刷新失败但已有 mappings 时继续并记录 warning，无 mappings 则拒绝启动。

黑盒扫描为本次扫描启动独立 `proxy.py` 子进程：

- 绑定 `127.0.0.1:<OS 分配端口>`。
- `HOST_MAP_JSON` 注入该扫描映射。
- DNS 插件按 host 查映射；未命中走默认 DNS。
- 强制单 worker/acceptor/local executor，避免 proxy 按 CPU fork 进程爆炸。
- 探活失败 fail-fast `PROXY_UNREACHABLE`。
- 扫描结束 SIGTERM→SIGKILL best-effort 清理。

该代理对 HTTP 请求和 HTTPS CONNECT 均生效，让 agent-browser / playwright-cli / HTTP 工具保持目标 Host 与 TLS SNI，同时落点使用快照 IP。

## 引擎注入差异

- **agent-browser**：代理通过每条命令的 `--proxy <url>` 注入；`write_config` 只负责 profile 目录。
- **playwright**：代理写入 per-session `cli.config.<session>.json` 的 `launchOptions.proxy`；`session_flag` 忽略 proxy 参数。
- **openai-agents 工具**：`ToolContext.proxy_url` 注入 bash 环境或 httpx client。
- **claude-agent-sdk CLI 子进程**：provider 设置 `HTTP_PROXY/HTTPS_PROXY`，`NO_PROXY` 保留 loopback。

认证测试也复用同一解析逻辑；每个 credential 可有独立代理，避免不同测试互相污染。

## 安全检查与降级

即使使用 host mapping，`resolve_and_pin_host` 仍会对映射 IP 执行 SSRF/loopback 检查；mapping 不是绕过安全策略的通道。

无代理的旧路径可将 URL pin 到 IP 并保留 Host header；有代理时保持原 URL，由代理解析，避免 TLS SNI 丢失。目标 preflight 失败按配置/环境类错误处理，不静默改打其他地址。

## 验证入口

- `packages/web/tests/test_host_profile_store.py`
- `packages/web/tests/test_api_host_profiles.py`
- `packages/core/tests/test_browser_proxy.py`
- `packages/core/tests/test_security_host_mapping.py`（若当前分支存在）
- `packages/core/tests/agents/tools_openai/test_proxy_injection.py`
