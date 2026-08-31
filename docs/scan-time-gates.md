# 扫描时间闸门全景（为什么会「扫不完」、哪些能放宽）

> 2026-08-31 盘点。一句话：**扫描从点开始到出报告，要过 5 道能弄死它的闸门；其中总闸 3 小时是最该放宽的，而所有单步时长都写死在代码里，env 调不了。**
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
- 位置：whitebox `workflows.py:257/337/411/428`、blackbox `workflows.py:395`。

### 3. 判链环节 15 分钟（2026-08-27 事故原样未改）

- 整个 chain verdict 环节（GitNexus 轨逐链深判）窗口 **15min**，写死在 `whitebox/pipeline/workflows.py:484`，被测试 `test_workflows_safety.py` 锁定。
- 容量公式：`链数 ÷ 并发 × 单链耗时`。链多就算不完，重试 3 次都算不完后——如果 LLM 轨是关的（`SUPERNOVA_LLM_TRACK_ENABLED=0`），**整场扫描直接判失败**。
- 窗口改不了，但分母（并发）能调：`SUPERNOVA_CHAIN_VERDICT_CONCURRENCY` 默认 4，调 8 = 同样 15 分钟容量翻倍。已有逐链 checkpoint 兜底（重试只补未判的链，不再全量重跑）。

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
SUPERNOVA_CHAIN_VERDICT_CONCURRENCY=8      # 判链并发 4→8，15min 窗口容量翻倍
SUPERNOVA_CHAIN_VERDICT_MAX_AGENTS=300     # 链数护栏 200，大仓超限链会不深判直接标保守
SUPERNOVA_GITNEXUS_VERDICT_MAX_TURNS=50    # 默认 30，轮尽=该链不深判
SUPERNOVA_CHAIN_VERDICT_MAX_TURNS=50
SUPERNOVA_MAX_CONCURRENT=4                 # 当前 .env 是 2；worker 8CPU/4G 下可到 4

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

另一句：当前 profile 是 `glm-anthropic`（claude 引擎），`SUPERNOVA_OPENAI_*` 那组现在**不生效**（claude 引擎单次调用无超时，兜底全靠 activity 窗口），别白调。

---

## 五、env 调不了、只能改代码的

| 卡点 | 位置 | 现值 |
|---|---|---|
| 判链窗口 | whitebox `workflows.py:484` | 15min（测试锁定） |
| code_index 窗口 | whitebox `workflows.py:30` | 20min |
| agent 类窗口 | whitebox `workflows.py:257/411` 等 | 2h |
| GitNexus CLI 子进程 | `gitnexus_engine.py:48` | 300s，超时 fail-fast 不降级 |
| GitNexus MCP 常量组 | `gitnexus_mcp.py:14-32` | 30s/120s/5s/30s/10s |
| 批量认证 probe | blackbox `workflows.py:881` | 硬编码 10min（单个的能 env 调、批量的不能，不一致） |

---

## 六、两个结构性隐患（建议排期修）

1. **关联扫描 activity 无 retry_policy**（见闸门 4）——下一个「扫不完」的定时炸弹。
2. **web 容器没有 restart 策略**（docker-compose.yml 里只有 worker 有）——web 崩一次，组合扫描的编排协程全丢，在跑的扫描要等下次重启才被补标 interrupted。

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
