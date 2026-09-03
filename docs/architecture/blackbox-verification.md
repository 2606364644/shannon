# 黑盒验证

黑盒验证是 exploitation-only 阶段：复用白盒/跨仓产出的攻击面与漏洞 queue，在 live target 上认证、确认端点、执行利用、收集证据并生成黑盒报告。它不做独立 recon，也不重新发明漏洞发现流程。

## 输入契约

黑盒需要：

1. `web_url`
2. 白盒 repo/path 或关联 workspace
3. `recon_deliverable.md`
4. 至少一个非空 `{vuln}_exploitation_queue.json`
5. 可选认证配置 / 认证档案展开结果
6. 可选 HOST 档案快照

`detect_whitebox_results` 会检查单仓 deliverables；指定 `correlated_workspace` 时，在单仓无结果的情况下把关联 workspace 的 merged queue 作为额外来源。即使 queue 命中，`recon_deliverable.md` 缺失仍 fail-fast——没有 API inventory/input vectors 时 exploit agent 会失明。

## Workflow

```text
setup display (web path)
  -> per-scan HOST proxy
  -> target preflight
  -> browser engine resolve + stealth/deny config
  -> authentication validation (optional)
  -> detect whitebox / correlation results
  -> endpoint live verification
  -> per-vuln queue validation
  -> exploit agents (bounded parallel)
  -> coverage gap close
  -> blackbox report_data
  -> markdown report + report agent + block verification
```

取消、heartbeat、进度事件和 session completed_agents 由 Temporal activity 与 Web event stream 维护。

## HOST 与 preflight

HOST 档案先解析成不可变映射快照，然后为本次扫描启动独立本地代理。后续浏览器、agent 工具和 HTTP preflight 都走该代理；未映射 host 由代理默认 DNS 解析。无 HOST 时不启动代理，目标仍要通过 URL 解析、SSRF 和 loopback 检查。

preflight 失败通常是目标不可达、代理不可用或配置错误，不进入 exploit 阶段。

## 认证

有认证配置时，`validate-authentication` agent 使用当前浏览器引擎执行登录：

- 表单、SSO、API、basic 等 login type 由认证配置/档案展开。
- 支持 TOTP 与邮箱二次登录。
- 保存 `auth-state.json`，后续 endpoint verify 和 exploit agent 恢复同一认证态。
- 多角色账户会写 `identity-manifest.json`；authz exploit 在至少两个 available 身份时进行越权对比。
- primary 身份必须成功；其他身份失败可记录为不可用，不必然阻断整场扫描。
- 引擎失败与目标登录失败分离，避免把 LLM 配置问题误报为账号密码错误。

认证结束会回收对应浏览器 session，避免长时间占住并发额度。

## 端点 live 验证

`endpoint-verify` 在 exploit 前运行，输入是所有 vuln queue 的端点全集。它只做 bounded reachability check，不爬虫、不 fuzz、不利用漏洞。

每个 `METHOD /path` 得到：

| live_status | 含义 |
|---|---|
| `live` | 业务响应/认证响应/服务端错误证明路由存在，记录 resolved_path |
| `not_live` | 源路径与合理前缀均 404，或连接失败/超时 |
| `param_invalid` | 路由存在但参数被 400/422 拒绝，需要 exploit 调整参数 |

路由前缀探测会考虑：

- base URL 自带 path；
- 已确认 live 路由的共同前缀；
- `/api`、`/v1`、`/v2`、`/app` 等常见前缀。

产物：`blackbox/intermediate/endpoint_verify.json`。

该功能是增强，不重试：agent 失败、超时或无结构化输出时不产文件，后续 exploit 按“全打”降级，保持旧行为。

## Queue gating

每个 selected vuln class 先经过 `ExploitationChecker.validate_queue`：

1. queue 文件存在。
2. JSON 可解析。
3. `vulnerabilities` 是 list。
4. 对应分析 deliverable 存在。
5. vulnerabilities 非空。

- 文件缺失/空 list 属于正常“不在范围”，跳过 agent。
- JSON/结构异常记录 warning 并跳过，避免把损坏输入交给 exploit。
- queue 有效才写 per-agent browser config 并调度 exploit。

## Exploit agents

对每个有效 vuln class 运行 `prompts/<vuln>-exploit.txt`：

- 读取该类 exploitation queue。
- 注入 endpoint verify 结果、跨仓 topology/boundaries（如有）。
- 恢复共享认证状态。
- 使用独立浏览器 session。
- 通过 `add_exploit` collector 逐条提交动态验证 verdict。
- 产物写入 `blackbox/<vuln>_exploitation_evidence.md` 与结构化 verdicts。

并发由 `SUPERNOVA_MAX_CONCURRENT`（默认 3）限制；单 agent 失败会记录，不中断其他类。agent 结束后立即回收该 agent 的浏览器 session，不等整个扫描 finally。

跨仓模式下，topology/trust boundary 只用于 exploit 上下文，不会把确定性单仓候选链伪装成已动态验证证据。

## 覆盖率与报告

`close_coverage_gaps` 对比 queue ID 与 evidence Markdown 标题：

- 已覆盖 ID：进入 evidence。
- 未覆盖 ID：显式写入未覆盖节，不静默丢失。
- `coverage_renderer` 可输出类别覆盖率。

报告链路：

1. `write_blackbox_report_data` 从动态 verdict 生成 `blackbox/report_data.json`，黑盒 PoC request/curl/raw_http 由实际请求确定性转录。
2. FindingsRenderer 渲染 evidence/findings。
3. ReportAssembler 拼装 `comprehensive_security_assessment_report.md`。
4. report agent 可润色，但漏洞节数量校验失败会自动重建底稿，防止压缩正文导致统计为 0。

白盒 `report_poc` 与黑盒动态 `PocBlock` 字段互不填充；前者是复制式攻击说明，后者带实际验证证据。

## 复用与运行

CLI 示例：

```bash
# 白盒带 URL，便于黑盒自动匹配
uv run supernova-whitebox start --repo <REPO> --url <URL>

# 复用最新白盒
uv run supernova-blackbox start --url <URL> --repo <REPO> --latest

# 指定 workspace / 不跑利用
uv run supernova-blackbox start --url <URL> --repo <REPO> -w <whitebox-workspace>
uv run supernova-blackbox start --url <URL> --repo <REPO> --no-exploit
```

辅助命令：

```bash
uv run supernova-whitebox workspaces
uv run supernova-blackbox workspaces
uv run supernova-blackbox workspace show <workspace>
uv run supernova-blackbox logs <workspace> --follow
```

如果黑盒日志显示 running recon from scratch，当前实现已经不再支持该路径，应视为契约/提示漂移并检查白盒 deliverables 是否能解析。

## 产物

```text
blackbox/
  intermediate/endpoint_verify.json
  <vuln>_exploitation_evidence.md
  intermediate/<vuln>_exploit_verdicts.json
  report_data.json
  comprehensive_security_assessment_report.md
```

## 验证入口

- `packages/blackbox/tests/pipeline/`
- `packages/blackbox/tests/` 中 endpoint verify、queue validation、coverage、report 相关测试
- `packages/core/tests/test_browser_proxy.py`
- `packages/core/tests/services/` 中 blackbox report data 相关测试
