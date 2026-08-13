# 黑盒扫描 HOST 配置修复设计

> 日期：2026-08-13
> 状态：draft，待 review
> 依赖：`docs/superpowers/specs/2026-08-12-blackbox-host-profile-design.md`
> 范围：修复已有 HOST 档案 + per-scan proxy 功能；不重新设计 per-scan proxy 基础方案。

---

## 0. 一句话结论

HOST 配置只对**黑盒阶段**生效：

- 纯黑盒扫描：生效；
- 组合扫描（`type=whitebox` 且带 `url`，后续接力黑盒）：生效；
- 纯白盒扫描（`type=whitebox` 且无 `url`）：不使用 HOST；
- correlation：不使用 HOST。

本 spec 修复以下闭环问题：

1. 认证校验和组合扫描 auth precheck 未使用 HOST proxy；
2. 纯黑盒 resume 丢失 HOST 映射；
3. 黑盒重跑没有恢复 HOST 配置；
4. HOST 解析失败会留下未提交但状态为 `running` 的 scan；
5. 前端启用 HOST 但未填写来源时静默退化为默认 DNS；
6. 手工档案允许非法 IP，错误延迟到扫描阶段才暴露。

---

## 1. 背景与当前问题

原始设计已经验证 per-scan proxy 可以将同一域名按扫描隔离到不同 IP。当前实现的初始、无认证黑盒链路基本成立，但存在生命周期和阶段一致性问题。

### 1.1 认证阶段未穿透 HOST

黑盒 workflow 虽然会在 preflight 前启动 proxy，但 `run_blackbox_auth_validation` 调用 `validate_authentication()` 时没有传递 `proxy_url`。组合扫描的 `_run_precheck()` 使用独立的认证验证 workflow，同样没有 `host_mappings`/`proxy_url`。

结果：目标或登录地址为内部域名时，认证阶段可能使用默认 DNS，导致认证失败或访问错误环境；即使 exploitation 阶段使用了正确 HOST，也无法弥补前置认证阶段的错误。

### 1.2 纯黑盒 resume 丢失映射

初次提交只保存 `reuse_whitebox_scan_id`，没有保存 `host_profile_id`、`host_url` 或解析后的映射。resume 时重新构造 `ScanRequest`，并未给 `_submit_blackbox()` 传 `host_mappings`。

结果：

```text
首次：target.test -> 10.0.0.2
resume：target.test -> 默认 DNS
```

### 1.3 重跑分为两种不同语义

系统存在两种“重跑”：

1. 组合扫描黑盒失败后的 `rerun-blackbox`：同一个 scan 继续尝试；
2. 扫描列表中的“重跑”：进入新建扫描页面，创建一个新 scan。

两者必须区别处理：

- 同一个 scan 的黑盒续跑必须使用原始 HOST snapshot；
- 新建 scan 的重跑表单只负责预填原 HOST 来源，新 scan 可以重新解析最新 profile。

### 1.4 HOST 解析失败留下 ghost scan

当前 scan 目录在 HOST 解析前创建，解析或 provider URL 拉取失败时，只清理内存中的 `_active_reqs`，没有将 session 标记为 failed，也没有写终态事件或删除未提交目录。

### 1.5 前端启用状态与实际行为不一致

前端允许用户点击“配置 HOST”后直接进入 enabled 状态，但 profile/url 为空时不会阻止提交。最终请求不包含 HOST 字段，后端会按“未启用 HOST”处理。

### 1.6 档案输入校验不足

`HostMapping.ip` 当前只是 `str`，手工编辑可以保存非法 IP；错误在黑盒 preflight 阶段才暴露。

---

## 2. 目标与非目标

### 2.1 目标

- 纯黑盒和组合扫描的黑盒阶段使用同一套 HOST 解析与传递规则；
- auth validation、组合 auth precheck、browser、bash/curl、web_fetch、endpoint verify 使用一致的 HOST 配置；
- 一个 scan 在首次运行、resume、Temporal retry、组合黑盒续跑期间固定访问同一个 HOST snapshot；
- HOST 配置错误在提交前明确失败，不创建 ghost scan；
- 已创建 scan 在 Temporal 提交后失败时有明确 failed 终态和清理路径；
- 前端不会显示“HOST 已启用”但实际走默认 DNS；
- 档案和直接 URL 输入都进行明确、可测试的校验；
- 保留未配置 HOST 的旧行为：不启动 proxy，按默认 DNS 访问。

### 2.2 非目标

- 不重新设计 `proxy.py`、`HostResolverPlugin` 或 per-scan 端口隔离方案；
- 不给纯白盒扫描增加 HOST 代理；
- 不给 correlation 增加 HOST 语义；
- 不通过修改容器级 `/etc/hosts` 实现映射；
- 不支持 wildcard host 映射；
- 不在扫描过程中动态切换 HOST 映射；
- 不处理 proxy 运行期间的长期健康探测。本次只保证启动探活和失败收尾；运行中探活另立议题。

---

## 3. 扫描类型与 HOST 边界

| 请求类型 | 是否有黑盒阶段 | HOST 字段 | 行为 |
|---|---:|---:|---|
| `blackbox` | 是 | 支持 | HOST 作用于整个黑盒 workflow |
| `whitebox` + `url` | 是，组合接力 | 支持 | HOST 只作用于 auth precheck 和后续黑盒阶段，不作用于白盒代码扫描 |
| `whitebox` 无 `url` | 否 | 前端不发送 | 不启动 HOST proxy |
| `correlation` | 否 | 前端不发送 | 不使用 HOST |

后端兼容规则：旧调用方即便向纯白盒或 correlation 误传 HOST 字段，也不因 HOST 字段本身失败；服务端忽略这些字段且不持久化、不启动 proxy。新前端不得发送这些字段。

### 3.1 HOST 来源互斥

黑盒或组合扫描中：

- `host_profile_id` 与 `host_url` 只能二选一；
- 两者都不填 = 未启用 HOST，走默认 DNS；
- 两者同时填 = 422；
- 显式启用但来源为空 = 前端阻止提交，后端也必须 422，不能静默降级。

---

## 4. 核心不变量

### 4.1 每个 scan 固定一个 HOST snapshot

HOST 在 scan 首次提交前解析为不可变 snapshot：

```json
{
  "source": "profile",
  "profile_id": "host_xxx",
  "source_url": "https://hosts.example/hosts.txt",
  "mappings": {
    "api.internal.example": "10.0.0.2",
    "admin.internal.example": "10.0.0.3"
  },
  "warnings": [],
  "resolved_at": 1786616539.0
}
```

要求：

- Temporal retry 不重新读取 profile；
- resume 不重新读取 profile；
- 组合 `rerun-blackbox` 不重新读取 profile；
- profile 后续修改不影响已创建 scan；
- 新建一个全新的 scan 时，才重新解析 profile/source URL。

### 4.2 映射命中和未命中

- URL hostname 命中 snapshot：经 per-scan proxy 解析到指定 IP；
- URL hostname 未命中 snapshot：经 proxy 的默认 DNS 解析；
- 未配置 HOST：不启动 proxy，保持旧行为；
- 已明确配置 HOST 但解析结果为空：失败，不允许静默走默认 DNS。

### 4.3 认证和黑盒阶段必须使用同一 snapshot

“同一 HOST 配置”指同一个 `{host: ip}` snapshot，不要求组合 auth precheck 和后续黑盒阶段共享同一个 OS proxy 进程。

- 纯黑盒：auth validation 与 exploitation 可以共享同一个 workflow 内 proxy；
- 组合扫描：auth precheck 是独立 workflow，可以启动独立 proxy，但必须接收相同 snapshot；
- 后续黑盒 workflow 再启动自己的 per-scan proxy，仍使用相同 snapshot。

---

## 5. HOST snapshot 持久化

在 scan 的 `session.json` 中增加统一字段：

```json
{
  "host_config": {
    "enabled": true,
    "source": "profile",
    "profile_id": "host_xxx",
    "source_url": "https://hosts.example/hosts.txt",
    "mappings": {
      "api.internal.example": "10.0.0.2"
    },
    "warnings": [],
    "resolved_at": 1786616539.0
  }
}
```

字段约束：

- `enabled=false` 时可以省略 `host_config` 或写 `null`；
- `source` 为 `profile` 或 `url`；
- `profile_id` 仅 profile 来源使用；
- `source_url` 对 profile 来源表示档案原始 source_url，对直接 URL 表示本次输入；
- `mappings` 是本次 scan 的最终快照，不是实时引用；
- 不保存认证凭据；
- `warnings` 只用于诊断和详情展示，不改变已经确定的 mappings。

### 5.1 详情与新建扫描重跑

scan detail 返回以下非敏感字段供前端预填：

```json
{
  "host_profile_id": "host_xxx",
  "host_url": null,
  "host_source": "profile",
  "host_mapping_count": 2
}
```

扫描列表中的“重跑”进入新建 scan 页面：

- profile 来源：预填 `host_profile_id`；新 scan 重新解析 profile；
- URL 来源：预填 `host_url`；新 scan 重新拉取 URL；
- 不把旧 snapshot 当成新 scan 的默认解析结果，除非产品另行提供“复用原始环境”选项。

### 5.2 组合黑盒续跑

`POST /{ws}/scans/{scan_id}/combined/rerun-blackbox` 不是新 scan，必须读取原 session 的 `host_config.mappings`，不重新 refresh。

如果 session 缺少 HOST snapshot：

- legacy scan 且没有 HOST 字段：按未配置 HOST 兼容；
- legacy scan 明确使用过 HOST 但 snapshot 损坏：失败并提示无法安全恢复，不能默认 DNS。

---

## 6. HOST 解析与提交顺序

### 6.1 推荐顺序

```text
校验 ScanRequest
  -> 解析 HOST profile / host_url
  -> 校验 mappings 非空和 IP/host 格式
  -> 创建 scan 目录
  -> 持久化 host_config snapshot
  -> 写认证配置
  -> 提交 Temporal workflow
```

这样 HOST 配置错误不会产生 scan 目录。

### 6.2 profile 来源

- profile 不存在：422；
- profile 无 `source_url`：使用落盘 mappings；
- profile 有 `source_url`：启动时尝试 refresh；
- refresh 成功且 mappings 非空：使用新 mappings，并更新 profile 快照；
- refresh 失败但原快照非空：使用旧 mappings，并记录 warning；
- refresh 失败且没有可用快照：422；
- refresh 成功但解析后无有效 mappings：422，不允许使用默认 DNS。

### 6.3 直接 URL 来源

- 只允许 `http` / `https`；
- 禁止自动跟随重定向；
- 读取超时和响应大小必须有限制；
- 解析结果包含 warnings 时允许继续，但必须记录 warnings；
- 没有任何有效 mapping：422；
- 本次直接 URL 默认只作用于当前 scan，不自动写入 HOST 档案库。

自动入库如果仍然需要，应另立设计，不能作为本修复的隐式副作用。

### 6.4 提交失败收尾

- HOST 解析失败：在创建 scan 前返回 422；
- proxy 启动失败、Temporal 连接失败或 workflow submit 失败：若 scan 已创建，必须写入 `failed`、`completed_at`、失败原因和 `scan_end`，并清理临时配置；
- 未提交 Temporal 的 scan 不得保持 `running`；
- `_active_reqs`、`_handles`、`_tasks` 必须同步清理。

---

## 7. 网络出口穿透矩阵

| 阶段/出口 | HOST 处理方式 | 验收要求 |
|---|---|---|
| 纯黑盒 auth validation | 使用 snapshot 启动/使用 proxy | 内部登录地址可按 HOST 登录 |
| 组合 auth precheck | 独立 auth workflow 接收 snapshot 并使用 proxy | precheck 和后续黑盒使用相同映射 |
| preflight | 使用 snapshot 解析 pinned IP，可直连 pinned IP | 不能回退到默认 DNS |
| agent-browser | `--proxy <proxy_url>` | 浏览器命中映射 IP |
| playwright-cli | `launchOptions.proxy.server` | 浏览器命中映射 IP |
| bash/curl | 子进程 `HTTP_PROXY` / `HTTPS_PROXY` | curl 命中映射 IP |
| web_fetch | httpx client 使用 proxy | web_fetch 命中映射 IP |
| endpoint verify | 复用 exploitation 的 proxy_url | verify 不得绕过 HOST |
| LLM provider 请求 | 维持现有 provider 网络策略；不得改变目标 HOST 语义 | 不因 HOST proxy 破坏 provider 调用 |

preflight 可以不经过 proxy，但必须使用同一个 snapshot 得到 pinned IP。实现不能把当前的 URL 字符串替换直接视为已经保留原始 SNI：
HTTPS 虚拟主机必须增加 Host/SNI 测试；如果直连 pinned IP 无法保留原始 SNI，则 preflight 改为经 per-scan proxy 探测。
其余目标 HTTP 请求必须经 per-scan proxy。

---

## 8. 输入校验与安全策略

### 8.1 IP

第一版只接受合法 IPv4：

- 允许 RFC1918 私网地址；
- 禁止 loopback、link-local、unspecified；
- 非法 IP 在档案保存和 scan request 解析阶段都必须失败；
- IPv6 暂不支持，避免 URL 替换和 proxy CONNECT 语义不一致。

如果需要 IPv6，必须增加独立测试矩阵后再开放。

### 8.2 host

- trim + lowercase；
- 不允许协议、端口、路径和 wildcard；
- 同一个 host 出现多个不同 IP 时拒绝保存；
- host 为空时拒绝保存；
- profile 和 `/etc/hosts` 解析共用同一套规范化规则。

### 8.3 provider URL

HOST provider URL 与目标 mapping IP 是两类安全边界：

- 目标 mapping IP 可以是私网地址；
- provider URL 必须经过 scheme、DNS、重定向、超时和响应大小校验；
- loopback、link-local、metadata 地址默认拒绝；
- 是否允许 provider URL 访问 RFC1918/Kubernetes 内网地址，需要部署级 allowlist 明确配置，不能由 HOST mapping 自动放开。

---

## 9. API 与前端契约

### 9.1 ScanRequest

黑盒或组合扫描允许：

```json
{
  "host_profile_id": "host_xxx"
}
```

或：

```json
{
  "host_url": "https://hosts.example/hosts.txt"
}
```

两者互斥。

纯白盒和 correlation 前端不发送 HOST；后端兼容误传但忽略。

### 9.2 前端校验

HOST 开关启用时：

- profile 模式必须有 `profileId`；
- URL 模式必须有合法 `http(s)` URL；
- 当前 workspace 未选择时不能加载/提交 profile；
- 未配置 HOST 时，状态显示“未启用”，且请求不发送 HOST 字段；
- 不允许显示“已启用”但最终请求为空。

### 9.3 API 路由命名

HOST 档案刷新实际契约统一为：

```text
POST /api/workspaces/{ws}/host-profiles/{pid}/refresh
```

spec 不再使用模糊的 `/refresh/{pid}` 表述。

---

## 10. 测试矩阵

### 10.1 解析和模型

- profile/url 互斥；
- blackbox 和 combined 接受单一 HOST 来源；
- pure whitebox/correlation 不启动 HOST；
- 非法 IP、空 host、重复 host 拒绝；
- `/etc/hosts` 注释、别名、非法行和 warnings；
- 无有效 mapping 时失败。

### 10.2 网络链路

- 同域名不同 scan 使用不同 IP；
- HTTP 和 HTTPS CONNECT；
- agent-browser；
- playwright-cli；
- curl/bash；
- web_fetch；
- preflight 使用 mapping IP；
- endpoint verify 使用 mapping；
- 未映射 host 保持默认 DNS。

### 10.3 认证

- 纯黑盒认证登录地址依赖 HOST 时成功；
- 组合 auth precheck 登录地址依赖 HOST 时成功；
- auth validation 没有 proxy 参数时测试必须失败，补齐后通过；
- 认证阶段和 exploitation 阶段使用同一 snapshot。

### 10.4 生命周期

- HOST 解析失败不创建 scan；
- Temporal submit 失败不会留下 running scan；
- pure blackbox resume 保留 mappings；
- combined resume 保留 mappings；
- combined `rerun-blackbox` 使用旧 snapshot；
- 扫描列表新建重跑正确预填 profile/url；
- proxy 启动失败后进程和临时文件均清理。

### 10.5 前端

- enabled + profile 空值不可提交；
- enabled + URL 空值或非法 URL 不可提交；
- disabled 不发送 HOST 字段；
- pure whitebox 不显示 HOST 配置区；
- combined 和 blackbox 显示 HOST 配置区；
- detail → new scan 正确预填 HOST 来源。

---

## 11. 验收标准

满足以下条件才认为修复完成：

1. 纯黑盒、无认证、profile/url 两种来源都能按映射访问目标；
2. 纯黑盒带认证时，登录阶段和 exploit 阶段都使用 HOST；
3. 组合扫描中，auth precheck 和黑盒阶段都使用 HOST；
4. 同一域名的两个并发 scan 可以访问不同 IP，互不串；
5. resume、Temporal retry、组合黑盒续跑不改变 HOST snapshot；
6. 扫描列表新建重跑能预填 HOST 来源；
7. HOST 解析、校验或提交失败不会留下未提交的 running scan；
8. 未配置 HOST 的旧扫描行为不变；
9. 纯白盒本身不启动 HOST proxy；
10. 定向测试覆盖本 spec 第 10 节，且无新增 ghost scan、默认 DNS 静默回退或认证阶段绕过 HOST 的路径。

---

## 12. 仍需产品确认的少数决策

以下不阻塞本修复的主链路，但应在实现前确认：

1. **IPv6**：本 spec 默认第一版拒绝 IPv6；如果必须支持，需要单独增加 IPv6 proxy、HTTPS、preflight 测试。
2. **provider URL 的 RFC1918 访问**：本 spec 默认需要部署级 allowlist；是否允许所有 workspace manager 直接访问内网 provider，需要安全负责人确认。
3. **直接 `host_url` 自动入库**：本 spec 默认不自动入库，避免扫描流程隐式修改档案库；如果保留自动入库，需要另行定义去重、并发写入和覆盖规则。
4. **运行中 proxy 健康探测**：本修复只保证启动探活和终态清理；是否在每个 activity 前探活另立议题。
