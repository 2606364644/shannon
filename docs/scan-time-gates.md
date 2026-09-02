# 扫描时间闸门全景（为什么会「扫不完」、哪些能放宽）

> 2026-08-31 盘点，2026-09-01 修订（NodeGoat-20260901-015018 组合扫描失败诊断驱动，修正 4 处：判链闸门的开轨行为、模型敏感性、MAX_CONCURRENT web 接线、OPENAI_\* 生效判断）。
> 一句话：**扫描从点开始到出报告，要过 5 道能弄死它的闸门；其中总闸 3 小时是最该放宽的，而所有单步时长都写死在代码里，env 调不了。**
> 当前 `.env` 没配任何超时变量，全部在吃代码默认值。

---

## 一、闸门长什么样（叠层）

```
[总闸]  整场扫描最多跑多久            run_timeout 3h（env 可调）
[单步]  每个环节最多跑多久            start_to_close_timeout（全部硬编码）
[重试]  超时后重试几次、隔多久重试    RetryPolicy（全部硬编码，max 3）
[内层]  单次 LLM 调用 / 子进程超时    env 大多可调
[判活]  web 多久没心跳算扫描死了      90s（env 可调）
```

关键事实：**单步时长全部硬编码**。Temporal 要求 workflow 代码里不能读 env（确定性约束，见 whitebox/blackbox `workflows.py` 头部注释）。env 能动的只有：总闸、判活、引擎内层超时、并发数。

---

## 二、真正会「扫不完」的 5 道闸门

### 1. 总闸：3 小时，到点直接掐死

- 位置：`packages/core/src/supernova_core/runtime/workflow_timeout.py:16`，env `SUPERNOVA_WORKFLOW_TIMEOUT_HOURS`，默认 3。
- 超了会怎样：Temporal 服务端判 TIMED_OUT，**扫描代码里的收尾逻辑根本不执行**，救不回来。web 侧 15 秒内发现，把扫描标 failed。不可恢复、不可续跑。
- 调法：`.env` 加 `SUPERNOVA_WORKFLOW_TIMEOUT_HOURS=6`。注意它不在工作区白名单里，只能全局设，不能按工作区覆盖。关联扫描会自动取 `max(env, 4.5h)`。

### 2. 数学冲突：单步 2h × 重试 3 次 > 总闸 3h

- agent 类单步（白盒 vuln / 黑盒 exploit / pre-recon / recon）窗口都是 **2h**，重试最多 3 次。2+2+2=6h，早就超 3h 总闸。
- 结果：**第 2 次重试跑到一半就被总闸掐死，白跑**。这也是「调大总闸」最直接的理由。
- 位置：whitebox `workflows.py:276/356/442/459`、blackbox `workflows.py:395`。

### 3. 判链环节 15 分钟（2026-08-27 事故原样未改；2026-09-01 同型复发）

- 整个 chain verdict 环节（GitNexus 轨逐链深判）窗口 **15min**，写死在 `whitebox/pipeline/workflows.py:515`，被测试 `test_workflows_safety.py`（断言 `timedelta(minutes=15)`）锁定——改窗口要同步改断言。
- 容量公式：`链数 × 单链耗时 ÷ 并发`。**单链耗时高度随模型变**（2026-09-01 实测：deepseek-v4-flash-coder 单链 mean 24s / 每轮 2.0s；同仓换 deepseek-v4-flash-0731 后单链 mean 133s / 每轮 8.7s，慢 4-5 倍）——**换模型 = 换容量，判链是否超窗口要按当前模型重算**。另注意换模型还受网关参数白名单约束（0731 直名接受 `thinking` 关断参数、coder 别名路由直接 400），见 `docs/llm-proxy/thinking-matrix.md`。
- 2026-09-01 复发实证（NodeGoat-20260901-015018，组合扫描 failed）：72 链 × 133s ÷ 4 并发 ≈ 40min 纯判定 > 3×15min 窗口有效容量（扣 backoff 空转 15min + 长尾塌陷后 ≈ 30-35min），3 次重试耗尽时只差 4 条链。
- **开 LLM 轨也救不回来（2026-09-01 修正）**：~~"LLM 轨关了才整场判失败"~~ 不准确——activity **超时异常**会直接冒泡打挂 workflow，绕过 `_decide_gitnexus_failfast` 的「开轨标红继续」分流。该次 LLM 轨 7 个 agent 全部成功、白盒 deliverable 已落盘，仍整场 failed 且组合扫描黑盒未跑。**超时耗尽 ≠ 优雅降级，此路径只能改代码**（异常转 `failed_classes` 语义）。
- 窗口改不了，但分母（并发）能调：`SUPERNOVA_CHAIN_VERDICT_CONCURRENCY` 默认 4，在 ws 白名单（工作区可配）。已有逐链 checkpoint 兜底（重试只补未判的链，不再全量重跑）。调高两个注意：① 8 路对网关的限流表现未实测（4 路时 429 仅 2/81 链）；② 与在途 llm-concurrency-governance change（全局 in-flight 上限，stable profile=4）方向相反，其落地后工作区高并发会被全局闸压回——**建议先 6 观察限流再上 8**。

### 4. 关联扫描：4h 单步 + 无限重试 + 4.5h 总闸

- 位置：`packages/multi/src/supernova_multi/pipeline/workflows.py:48-51`。
- 这个 4 小时的 activity **没设 retry_policy**——Temporal 默认无限重试；又没有 checkpoint，每次重入从头跑全部 4 小时；总闸只给它留了 30 分钟余量。**超时一次重入就必撞墙，全灭。**
- 全仓最脆的一处，只能改代码（补 retry_policy 或缩短窗口）。

### 5. 心跳判活 90 秒（误杀门）

- 位置：`scan_liveness.py:48`，env `SUPERNOVA_SCAN_LIVENESS_SECONDS`。
- worker 每 30 秒写一次心跳，超过 90 秒没写，web 就把扫描标 `interrupted`——**终态，不可逆**。worker 容器重启、被 OOM 杀一次，活着的扫描也会中招。
- 环境抖的话调到 180；配套的提交宽限 `SUPERNOVA_SCAN_LIVENESS_SUBMIT_GRACE_SECONDS` 默认 120s（排队超过 2 分钟还没轮到也会被怀疑），可调 300。

---

## 三、值得放宽 / 该调的 env（可直接抄）

```bash
# A. 放宽窗口
SUPERNOVA_WORKFLOW_TIMEOUT_HOURS=6         # 3h→6h，最该调的一个（TIMED_OUT 不可恢复，宁大勿小）
SUPERNOVA_LLM_PER_CALL_TIMEOUT=360         # 默认 60 太紧，官方 .env.example 推荐 360
SUPERNOVA_SCAN_LIVENESS_SECONDS=180        # 防容器抖动误杀
SUPERNOVA_SCAN_LIVENESS_SUBMIT_GRACE_SECONDS=300

# B. 提吞吐（对改不了的窗口的间接解法：窗口不动，让窗口内干完更多活）
SUPERNOVA_CHAIN_VERDICT_CONCURRENCY=6      # 判链并发 4→6（2026-09-01 修订：先 6 观察 429 再上 8；8 未实测限流且与 llm-concurrency-governance 全局闸方向冲突）。0731 慢模型下 6 路×3 窗口 ≈ 270 并发分钟 > 需求 ~160
SUPERNOVA_CHAIN_VERDICT_MAX_AGENTS=300     # 链数护栏 200，大仓超限链会不深判直接标保守
SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS=50    # 默认 30，轮尽=该链不深判
SUPERNOVA_CHAIN_VERDICT_MAX_TURNS=30       # (2026-09-01) 保持 30：实测 p90=24 轮未卡瓶颈（mean 14），调高只让长尾链占槽更久、拖垮并发利用率，慢模型下适得其反
SUPERNOVA_MAX_CONCURRENT=4                 # 当前 .env 是 2；worker 8CPU/4G 下可到 4。(2026-09-01) ⚠️ web 提交路径未接线——该值只对 CLI 直跑生效，web 扫描走 dataclass 默认 3（llm-concurrency-governance change 实证，待其接线）

# C. 抗网关抖动（2026-08-28 网关 5s 断连曾致补召回整层丢失）
SUPERNOVA_LLM_TRANSIENT_RETRIES=2          # 默认 1
SUPERNOVA_LLM_TRANSIENT_RETRY_DELAY=20     # 默认 10s

# D. 认证预检（这个支持按工作区覆盖）
SUPERNOVA_AUTH_VALIDATION_TIMEOUT_SECONDS=600  # 3 次全超时白烧 30min 还会挡住组合扫描启动
```

改完要重启 worker/web 容器才生效。

---

## 四、千万别放宽的（血泪史）

| 项 | 现值 | 为什么别加 |
|---|---|---|
| Temporal 重试次数 | 全线 max 3 | 从 50→8→3 一路砍下来的：曾 50×6min≈5h 卡死、8×10min≈80min 卡死、PoC 重入 1h43m |
| `SUPERNOVA_OPENAI_MAX_RETRIES` | 1 | 超时是幂等的，重试只是再超时一遍（曾 stall 4×300s≈20min 拖死主 agent） |
| PRODUCTION_RETRY 退避 5min→30min | — | 保持。判链要优化的话是给它换短退避档，不是放大窗口 |

另一句（2026-09-01 修订）：~~"当前 profile 是 glm-anthropic，OPENAI_* 不生效"~~ **不能按 profile 一刀切**——工作区层可覆盖 provider（`__legacy__` 工作区 8-31 18:23 起 `ai_provider: openai_compatible` + deepseek，当前扫描全走 openai 引擎，OPENAI_* 组**全部生效**）。判断哪组变量生效，看工作区 `config.yaml`/env 文本框的实际 provider，别看全局 profile。

---

## 五、env 调不了、只能改代码的

| 卡点 | 位置 | 现值 |
|---|---|---|
| 判链窗口 | whitebox `workflows.py:515` | 15min（测试锁定；慢模型/大链池下数学上不够，见闸门 3） |
| code_index 窗口 | whitebox `workflows.py:30` | 20min |
| agent 类窗口 | whitebox `workflows.py:276/356/442/459` | 2h |
| GitNexus CLI 子进程 | `gitnexus_engine.py:48` | 300s，超时 fail-fast 不降级 |
| GitNexus MCP 常量组 | `gitnexus_mcp.py:14-32` | 30s/120s/5s/30s/10s |
| 批量认证 probe | blackbox `workflows.py:881` | 硬编码 10min（单个的能 env 调、批量的不能，不一致） |

---

## 六、三个结构性隐患（建议排期修）

1. **关联扫描 activity 无 retry_policy**（见闸门 4）——下一个「扫不完」的定时炸弹。
2. **web 容器没有 restart 策略**（docker-compose.yml 里只有 worker 有）——web 崩一次，组合扫描的编排协程全丢，在跑的扫描要等下次重启才被补标 interrupted。
3. **判链超时异常绕过降级编排**（2026-09-01 新增，见闸门 3）——`run_gitnexus_chain_verdict` 重试耗尽抛 ActivityError 直接冒泡打挂 workflow，不走 `failed_classes`→`_decide_gitnexus_failfast` 的开轨标红继续路径；开轨模式下 LLM 轨成果全部作废、组合扫描黑盒不跑。修法：异常在 workflow 侧捕获转 `failed_classes` 语义。

---

## 七、附：全链路闸门时间线（速查）

| 时刻 | 闸门 | 默认 | 超了会怎样 |
|---|---|---|---|
| t=0 | temporal 探针 / 并发上限 | 1s / 4 个 | 提交被拒，扫描根本不开始 |
| t=0 | 提交宽限 | 120s | 过了还没轮到就开始怀疑孤儿 |
| 组合 t0 | 认证预检 | 10min×3 | 全超时白烧 30min，组合扫描 fail-fast 不跑白盒 |
| 运行中 | 心跳判活 | 90s | 标 interrupted（不可逆） |
| 运行中 | 单步窗口 | 2min～4h 不等 | 重试最多 3 次，耗尽标 failed 或整场终止 |
| 运行中 | 隐形消耗 | — | 富化/PoC 等非致命环节各 20-30min×3，失败不报错但吃总闸时长 |
| 运行中 | **总闸** | **3h** | **TIMED_OUT 硬死，不可恢复** |
| 取消后 | terminate 保险丝 | 60s | 软停不听就硬杀 |
| 收尾 | SSE 关流 | 10s + 300s 空闲 | 只影响 live 页面，不停扫描 |
