# 认证测试「停止」按钮（批量 + 单角色）

- 日期：2026-08-17
- 状态：已实现（TDD，2026-08-17）
- 范围：认证档案测试登录（批量 `test-batch` + 单角色 `testCredential`）补**用户主动停止**能力
- 涉及包：`web`（前后端）；blackbox worker **零改动**
- 演进自：`2026-08-11-auth-verify-batch-role-test-design.md`（批量测试）

---

## 1. 问题与目标

认证测试一旦发起无法停止：

- **批量页**（`AuthProfileTestPage`，路由 `/p/:ws/auth-profiles/:pid`）：`POST test-batch` 起 `BatchAuthValidationWorkflow`（Temporal 串行 N cred），只有「开始」按钮（`AuthProfileTestPage.tsx:211`），testing 时禁用转圈，无停止。
- **单角色页**（`VerifyProcessPage`，路由 `/p/:ws/auth-profiles/:pid/credentials/:cid`）：`testCredential` 起 `AuthValidationWorkflow`，同样无停止（`VerifyProcessPage.tsx:101-117`）。
- 后端 API（`api/auth_profiles.py`）无任何取消端点；`scan_manager` 的 `cancel()` 只服务 scan。

选错角色/选错 HOST/误触发起后，只能等全部跑完（每 cred 最长 10 分钟）或重启服务。

**目标**：两个页面在 testing 期间提供「停止」按钮 → 取消对应 Temporal workflow → 状态立即翻转、后续角色不再开始。

**非目标**：
- 强杀在跑探针子进程（见 §7 已知限制，YAGNI）。
- `AuthProfilesPage` 列表行的取消入口（测试只从上述两页发起）。
- 新增 `cancelled` 终态（已确认折中语义，见 §2）。

---

## 2. 取消后状态语义（已确认）

用户主动停止时：

- **在跑的 cred**（恰好 1 个，串行语义）：标 `failed`，`failure_point="cancelled"`，`failure_detail="用户取消测试"`。红色徽章可接受（该角色确实没测完）。
- **未开始的 cred**（仍 `unverified`）：**保持 `unverified` 不动**——没测≠失败。现状 watcher 兜底会把它们也标 `failed/out_of_band`，属语义 bug，本设计一并修正（§4）。
- **已完成 cred**：不动（幂等，不重写）。

**已完成的 cred 的 events.ndjson / probe_dir 一律保留**（与正常终态一致，供回看）；在跑 cred 的 `scan-config.yaml` 删除（密码卫生，复用 `_apply_batch_cred_terminal` 清理）。

**备选与否决**：新增 `cancelled` 状态被否决（改动面大：store Literal + 前端类型/徽章/overall + i18n，收益仅是徽章颜色）；全标 `failed` 被否决（未测角色显示失败，误导）。

---

## 3. 后端：取消端点

### 3.1 路由

`POST /workspaces/{ws}/auth-profiles/{pid}/cancel-test`，body `{"workflow_id": string}`（动作型 POST，对齐 `test-batch` 风格与 `POST /{ws}/scans/{scan_id}/cancel` 语义）。

### 3.2 scan_manager.cancel_auth_validation(ws, pid, workflow_id)

顺序**先回填状态、后 cancel workflow**（Temporal 不可达时状态也不卡 running）：

1. **越界守护**：`workflow_id` 必须以 `authval-{ws}-` 或 `authval-batch-{ws}-` 开头（对齐既有守护，`scan_manager.py:1141`），且该 pid 档案下确有 cred 的 `verify_status.workflow_id == workflow_id`；不满足 → `ValueError`（端点转 422）。
2. **即时回填**：对绑此 workflow 且 `state=="running"` 的 cred 写 `VerifyStatus(state="failed", failure_point="cancelled", failure_detail="用户取消测试", last_verified_at=now, probe_dir, workflow_id)`，删其 `scan-config.yaml`（越界 probe_dir 不动，复用 `_apply_batch_cred_terminal` 的守护与清理）。批量串行下恰好 1 个 running cred；单 cred 同样 1 个。
3. **Temporal 取消**：`handle.cancel()` best-effort（workflow 已终态或 Temporal 抖动 → 吞异常，不影响已回填状态）。
4. 无 running cred（已结束/从未跑过）→ 幂等返回，不报错。

返回 `{"cancelled": workflow_id}`；幂等命中时附 `"already_finished": true`。

### 3.3 端点（api/auth_profiles.py）

`cancel-test` → `sm.cancel_auth_validation(...)`；`ValueError` → 422（对齐 `test-batch` 的错误映射）。

---

## 4. 后端：watcher 兜底规则收紧

`_backfill_batch_from_result`（`scan_manager.py:1060`）现状：workflow 非 COMPLETED 终态时把 `cred_probe_map` 中**所有未回填** cred 标 `failed/out_of_band`——用户取消或 workflow 崩溃时，未开始的 cred 也被误标失败。

**改为**：只回填 store 中**当前仍 `running`** 的项（回填前读 profile 现态过滤）。效果：

- 未开始（`unverified`）→ 不动，保持 `unverified`。
- 在跑（`running`）→ 端点或 watcher 谁先到谁写 `failed`，后到者见非 running 跳过（幂等，无竞态覆盖）。

对 workflow 崩溃（非用户取消）场景同样正确：没开始的角色本来就不该标失败。`reconcile_auth_validation`（`scan_manager.py:1111`）只扫 running cred，天然一致，零改动。

---

## 5. 前端

### 5.1 API 层（api/authProfiles.ts）

新增 `cancelTest(ws, pid, workflowId)` → `POST .../auth-profiles/{pid}/cancel-test` body `{workflow_id}`。

### 5.2 批量页（AuthProfileTestPage）

- **按钮**：testing 期间 header 的「开始」按钮位置换成「停止」（destructive 变体，`Square` 图标 + 文案）；testing 结束恢复「开始」。workflow_id 取 `batchWfId ?? 当前 running cred 的 verify_status.workflow_id ?? null`（后者覆盖关页面重进后靠轮询恢复的场景），皆无则禁用。
- **onStop**：`cancelTest` → 成功 toast「已停止测试」→ `setRefreshTick+1` 立即重拉。既有机制自动收尾：running 消失 → `liveRun` 清空（`AuthProfileTestPage.tsx:138-140`）→ `VerifyLivePanel` 卸载 → SSE 关闭（`scanEventStore` 末订阅者卸载即 `es.close()`）；轮询见全终态自动停（`:102-108`）。取消后 in-flight cred 无 `scan_end`，SSE 面板靠卸载关闭（不依赖 `scan_end` 关流路径）。
- 请求失败 → toast 错误，按钮恢复可重试。

### 5.3 单角色页（VerifyProcessPage）

同款：testing 时「测试登录」按钮换「停止」，onStop 调 `cancelTest(workspace, pid, wfId)`（`wfId` = 该 cred `verify_status.workflow_id`）→ `setTesting(false)` + `setLiveRun(null)`（卸载 SSE 面板）→ `setRefreshTick+1` 重拉 profile 落 failed 态。

### 5.4 i18n

`zh.json` / `en.json` 补：`authProfiles.testPage.stop`（停止测试 / Stop test）、`authProfiles.verify.stop`（停止 / Stop）、`authProfiles.testPage.stopped`（已停止测试 / Test stopped）。

---

## 6. 测试

- **后端**（扩展 `test_api_auth_profiles.py` / `test_batch_auth_validation.py`）：
  - 端点：越界 workflow_id → 422；跨档案/跨 ws 前缀 → 422；幂等（无 running）→ `already_finished`；running cred → failed + `scan-config.yaml` 删除 + events.ndjson 保留 + unverified 兄弟不动。
  - watcher 规则：workflow 非 COMPLETED 终态回填时，`unverified` cred 不被标 failed，`running` cred 被标 failed。
- **前端**（扩展 `AuthProfileTestPage.test.tsx` / `VerifyProcessPage.test.tsx`）：testing 时按钮切换与禁用条件；stop 调 `cancelTest`；成功后轮询终止、SSE 面板卸载；失败 toast 可重试。

---

## 7. 已知限制

- **在跑探针不被强杀**：activity 无心跳，Temporal 取消只在活动间隙生效。当前 cred 的探针子进程后台跑完（最长 10 分钟）后结果丢弃（watcher 只回填 running，届时已非 running，不覆盖 failed）。停止的即时效果 = 状态翻转 + 后续角色不再开始。强杀需 pid 跟踪/心跳改造，YAGNI 不做。
- **取消后 token 浪费**：同上，in-flight cred 的 LLM 调用会继续到自然结束。

---

## 8. 实现与设计的偏差（2026-08-17 TDD 实施时确认）

1. **未开始 cred 的 scan-config 清理由 watcher 承担**（非端点）：未开始 cred 的 probe_dir 不在
   store（只有 running 过的 cred 才写），端点无从定位其明文配置；watcher 终态回填时按
   cred_probe_map 清（`_delete_probe_scan_config`）。清理语义与 `reap_stale_probes` 启动期兜底一致。
2. **批量页停止后轮询退出是显式的**：新语义下未开始 cred 保持 unverified，「全终态」退出条件
   永不满足 → `onStop` 成功后直接 `setTesting(false) + setPolling(false)`（用户停止即批次终结）。
3. **前端 `failure_point` 类型联合扩展 `"cancelled"`**（`api/types.ts`）：后端新写的枚举值需要
   类型对齐；测试 fixture 直接受益。
4. **watcher 回填对「有真实 result」的场景不加过滤**：仅 result() 抛（取消/崩溃，无 per-cred
   结果可依）时才限定只写 running cred；COMPLETED 且有 result 时全量回填（真实数据非猜测，
   未开始 cred 若有结果仍应回填——COMPLETED 批次所有选中 cred 必然都跑过）。
