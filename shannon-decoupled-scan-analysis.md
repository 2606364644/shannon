# Shannon 解耦白盒→黑盒扫描：问题清单

> 聚焦影响安全扫描效果的问题，置于报告最前；其他 bug 和体验问题列于其后。

---

## 一、影响安全扫描效果的问题（按严重度排序）

### S1. 路由不修正，网关前缀场景结构性漏报【严重】

白盒 recon 从源码静态分析得到的路由是相对路径（如 `/api/users/{id}`），存入 `*_exploitation_queue.json`。黑盒阶段**完全不重新发现路由**，直接复用白盒产物。

- **代码层零修正**：搜索 `gateway` / `basePath` / `mountPath` / `url.resolve` 等关键词在 `apps/worker/src/` 下无任何路由拼接或归一化代码
- **拼接交给 LLM**：[prompt-manager.ts:399-402](file:///workspace/apps/worker/src/services/prompt-manager.ts#L399-L402) 只做 `{{WEB_URL}}` 字符串替换，无路径拼接
- **404 → FALSE_POSITIVE → 不上报**：所有 exploit prompt 明确"FALSE POSITIVE findings... DO NOT include them in the deliverable"（如 [exploit-injection.txt:306,370](file:///workspace/apps/worker/prompts/exploit-injection.txt#L306)），[exploit-renderer.ts:159](file:///workspace/apps/worker/src/services/exploit-renderer.ts#L159) 对无 verdict 类只渲染"无定论"
- **项目自己承认**：[recon-static.txt:205](file:///workspace/apps/worker/prompts/recon-static.txt#L205) "These endpoints... not necessarily only those reachable in the current deployment"

**后果**：实际部署有网关前缀（`/v2/api/users` vs 代码里的 `/api/users`）的漏洞，黑盒 404 后大概率归 FALSE_POSITIVE，从最终报告消失。无路由可达性 preflight、无前缀探测、无爬虫兜底。

---

### S2. 黑盒静默以未认证状态跑 exploit，漏报需认证漏洞【严重】

[validate-authentication.ts:88-91](file:///workspace/apps/worker/src/services/validate-authentication.ts#L88-L91)：

```typescript
if (!authentication) return ok(undefined);
```

白盒 workflow 跳过认证阶段（白盒本就不登录），黑盒必须自己跑 `runAuthenticationValidation`。但黑盒独立加载 config，**不校验 config 中 `authentication` 字段是否非空**。

**后果**：用户白盒 config 含认证、黑盒忘了带 config 或用了缺认证字段的 config，黑盒静默以匿名身份跑 exploit。所有需认证才能访问的端点上的漏洞**全部漏报**，且无任何告警。

---

### S3. deliverables 校验不完整，exploit 质量下降而不报错【严重】

[activities.ts:1326-1357](file:///workspace/apps/worker/src/temporal/activities.ts#L1326-L1357) 的 `validateDeliverablesExist` 只校验：
- `recon_deliverable.md` 存在
- 至少一个非空 `*_exploitation_queue.json`

但 [exploit-injection.txt:79-83](file:///workspace/apps/worker/prompts/exploit-injection.txt#L79-L83) 等 exploit prompt **强制读三个文件**：

```
1. .shannon/deliverables/pre_recon_deliverable.md - Architecture overview, exact vulnerable code snippets
2. .shannon/deliverables/recon_deliverable.md - Complete API inventory, input vectors
3. .shannon/deliverables/injection_analysis_deliverable.md - Strategic context from analysis specialist
```

`pre_recon_deliverable.md` 和各类 `*_analysis_deliverable.md` **都不在校验集**。

**后果**：用户手动复制 deliverables 时漏了 `pre_recon_deliverable.md`，黑盒能正常启动，但 exploit agent 读不到架构上下文，**exploit 质量下降而无任何报错**。

---

### S4. vuln 类静默丢失，用户不知道为何少跑【中】

[queue-validation.ts:106-124](file:///workspace/apps/worker/src/services/queue-validation.ts#L106-L124) 要求 `*_analysis_deliverable.md` 和 `*_exploitation_queue.json` **成对存在**：

```typescript
function getExistenceErrorMessage(existence: FileExistence): string {
  if (!deliverableExists && !queueExists) {
    return 'Analysis failed: Neither deliverable nor queue file exists. Both are required.';
  }
  ...
}
```

但 [activities.ts:1341-1346](file:///workspace/apps/worker/src/temporal/activities.ts#L1341-L1346) 收到 err 时只 `continue` 跳过：

```typescript
for (const vt of ALL_VULN_CLASSES) {
  const result = await validateQueueSafe(vt, delivPath);
  if (isOk(result) && result.value.shouldExploit) {
    typesWithQueues.push(vt);
  }
  // err 时静默 continue
}
```

**后果**：用户只复制 queue 不复制 analysis deliverable（反之亦然），该类 exploit **静默跳过**，错误信息只在 worker 日志里 warn。最终报告里这一类漏洞完全消失，用户无从排查。

---

### S5. 黑盒报告缺失架构图与攻击面章节【中】

[reporting.ts:29-69](file:///workspace/apps/worker/src/services/reporting.ts#L29-L69) 的 `assembleFinalReport` 只拼接 5 个 per-class 文件（evidence/findings）：

```typescript
const deliverableFiles: readonly DeliverableFile[] = [
  { name: 'Injection', paths: ['injection_exploitation_evidence.md', 'injection_findings.md'], required: false },
  // ... 5 个类，无 pre_recon / recon 章节
];
```

`report-executive.txt` 让 report agent 读 `pre_recon_deliverable.md` / `recon_deliverable.md` 作为写摘要的上下文，但**不把它们作为独立章节拼进最终报告**。

**后果**：黑盒报告自包含性差，看不到白盒的架构分析和端点清单。审计者必须翻白盒 workspace 才能完整理解攻击面。

---

### S6. 新 workspace 场景未被 exploit 类在报告里消失【中】

[activities.ts:800-808](file:///workspace/apps/worker/src/temporal/activities.ts#L800-L808) 在 `exploit=true` 时跳过 `renderFindingsFromQueues`，不从 queue 重新生成 `*_findings.md`。新 workspace 场景下白盒的 `*_findings.md` 也没被复制过来。

**后果**：未被黑盒 exploit 的 vuln 类（如某类 queue 为空、或被 `vuln_classes` 排除），在最终报告里**完全消失**——既无 evidence 也无 findings。同 workspace resume 场景则 fallback 到白盒 findings.md，相对完整。

---

## 二、关联不顺畅 / 流程衔接问题

### P1. 黑盒 deliverables 种子代码读错路径【高，真实 bug】

[start.ts:79-92](file:///workspace/apps/cli/src/commands/start.ts#L79-L92)：

```typescript
if (args.blackboxOnly) {
  const srcDir = path.join(repo.hostPath, '.shannon', 'deliverables');
  ...
}
```

但 [start.ts:94-98](file:///workspace/apps/cli/src/commands/start.ts#L94-L98) 把这个目录当作空挂载点预创建：

```typescript
const shannonDir = path.join(repo.hostPath, '.shannon');
for (const dir of ['deliverables', ...]) {
  fs.mkdirSync(path.join(shannonDir, dir), { recursive: true });
}
```

白盒实际把 deliverables 写到 `<workspacesDir>/<workspace>/deliverables/`（通过 [docker.ts:277](file:///workspace/apps/cli/src/docker.ts#L277) 的 bind mount），不是 `repo.hostPath/.shannon/deliverables/`。

| 黑盒场景 | 结果 |
|---|---|
| 用相同 `-w my-audit`（resume 同 workspace） | bind mount 复用 → 工作 |
| 用不同 `-w` 或不带 `-w` | 种子读空目录 → `MissingDeliverablesError` |
| 手动复制 deliverables 到 `repo.hostPath/.shannon/deliverables/` | 工作 |

---

### P2. 文档示例命令链不可用【高】

[docs/whitebox-blackbox-scan.md:166-185](file:///workspace/docs/whitebox-blackbox-scan.md#L166-L185)：

```bash
./shannon start -r my-repo --whitebox-only
cat my-repo/.shannon/deliverables/comprehensive_security_assessment_report.md  # 主机找不到
./shannon start -u https://target -r my-repo --blackbox-only                  # 不带 -w 失败
```

`cat` 在主机上找不到文件，不带 `-w` 的黑盒因 P1 失败。用户按文档操作会卡住。

---

### P3. 黑盒不自动读白盒 scope 限定 vuln_classes【中】

[workflows.ts:979-982](file:///workspace/apps/worker/src/temporal/workflows.ts#L979-L982) 的 `validateDeliverablesExist` 遍历 `ALL_VULN_CLASSES` 找非空 queue，**不读 session.json 里白盒锁定的 `scope.vulnClasses`**。

**后果**：白盒用 `vuln_classes: [injection, auth]`，黑盒忘了带 config，黑盒尝试找 5 个 queue 但只找到 2 个——其他 3 个静默跳过。用户不知道为什么只跑了 2 个 exploit。

---

### P4. scope 锁定在黑盒 resume 路径被绕过【低】

[workflows.ts:936-950](file:///workspace/apps/worker/src/temporal/workflows.ts#L936-L950)：

```typescript
if (input.resumeFromWorkspace) {
  resumeState = await a.loadResumeState(...);  // 不调 persistOrValidateRunScope
} else {
  await a.persistOrValidateRunScope(activityInput, [], true);  // 只在新 workspace 路径
}
```

[activities.ts:998](file:///workspace/apps/worker/src/temporal/activities.ts#L998) 的 JSDoc 说 "resume runs throw if it differs"，**与实现不一致**。session.json 永远显示白盒的 `{exploit: false}`，与实际跑过的黑盒 exploit 不符，影响审计可读性。

---

## 三、明显可优化的点

| # | 优化点 | 代码位置 |
|---|---|---|
| O1 | `validateDeliverablesExist` 应校验 exploit prompt 实际依赖的完整文件集（pre_recon + recon + analysis + queue），提前 fail-fast 而非运行时降质 | [activities.ts:1326-1357](file:///workspace/apps/worker/src/temporal/activities.ts#L1326-L1357) |
| O2 | 黑盒应从 session.json 读白盒的 `scope.vulnClasses`，自动限定范围，不依赖用户传相同 config | [workflows.ts:979-982](file:///workspace/apps/worker/src/temporal/workflows.ts#L979-L982) |
| O3 | 种子代码应读 `<workspacesDir>/<prior-whitebox-ws>/deliverables/`，或 CLI 自动检测同 repo 最近白盒 workspace 并提示用相同 `-w` | [start.ts:79-92](file:///workspace/apps/cli/src/commands/start.ts#L79-L92) |
| O4 | `validateQueueSafe` 跳过时应在用户可见输出报告原因（哪一类、为什么），不只 warn | [activities.ts:1341-1346](file:///workspace/apps/worker/src/temporal/activities.ts#L1341-L1346) |
| O5 | 黑盒应在白盒 config 含认证但黑盒 config 缺认证时 warn 或 fail-fast | [workflows.ts:967-971](file:///workspace/apps/worker/src/temporal/workflows.ts#L967-L971) |
| O6 | 黑盒 resume 后应更新 session.json 的 scope 为 `{vulnClasses: [], exploit: true}`，并修正 [activities.ts:998](file:///workspace/apps/worker/src/temporal/activities.ts#L998) 的 JSDoc | [workflows.ts:936-950](file:///workspace/apps/worker/src/temporal/workflows.ts#L936-L950) |
| O7 | 文档应修正示例：明确黑盒必须用相同 `-w`，或说明手动复制 deliverables 的完整步骤 | [docs/whitebox-blackbox-scan.md:166-185](file:///workspace/docs/whitebox-blackbox-scan.md#L166-L185) |
| O8 | `assembleFinalReport` 应可选包含 pre_recon/recon 摘要章节，让黑盒报告自包含 | [reporting.ts:29-69](file:///workspace/apps/worker/src/services/reporting.ts#L29-L69) |
| O9 | 黑盒 workflow 新增路由可达性 preflight / 网关前缀探测 activity，缓解 S1 | `blackboxPipelineWorkflow` |

---

## 四、确认正确的设计（避免误判）

| 项 | 结论 |
|---|---|
| 黑盒是否误跳过 exploit | 不会。白盒 completedAgents 不含 `-exploit` agent，黑盒 `shouldSkip` 对所有 exploit 返回 false |
| 黑盒是否 restore git checkpoint | 不调 `restoreGitCheckpoint`，正确保留白盒 deliverables |
| 白盒是否跑认证 | 不跑，黑盒重新认证是正确设计 |
| 黑盒重复跑是否正确跳过 | 是。`loadResumeState` 交叉校验 deliverable 存在性，丢失则重跑 |
| 同 workspace resume 是否安全 | 是。`terminateExistingWorkflows` 会终止旧 workflow，避免并发 |

---

## 优先级建议

**最该立即修的三件事**（直接影响扫描结果正确性）：

1. **S1 + O9**：路由修正机制——网关前缀场景漏报是结构性问题，建议在黑盒 workflow 增加 preflight 阶段探测 base path / 校验路由可达
2. **S2 + O5**：黑盒认证配置校验——加 fail-fast 或强烈 warn，避免静默漏报需认证漏洞
3. **S3 + S4 + O1 + O4**：deliverables 完整性校验——把 exploit prompt 实际依赖的文件集纳入校验，跳过时显式报告原因
