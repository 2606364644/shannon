# 组合漏洞分析逻辑：二阶存储中转污点

> stored XSS / 二阶 SQLi / 运行时配置二阶 的确定性召回机制
> 分支 `feat/fork-py`。设计 spec：[`superpowers/specs/2026-07-21-second-order-storage-taint-dual-track-design.md`](../superpowers/specs/2026-07-21-second-order-storage-taint-dual-track-design.md)（GitNexus 轨子项⑤）。
> 统筹对比文档：[`scan-effectiveness-gains-vs-ts.md`](./scan-effectiveness-gains-vs-ts.md) §4.5。
> 本文回答四个问题：**谁写锚点 / 产物是什么 / 谁用 / 覆盖哪些漏洞与场景**。

---

## 0. 什么是"组合漏洞"（二阶存储中转）

"组合漏洞"在项目里就是 **二阶（second-order）漏洞**。它不是单点 sink 漏洞，而是由两个分离的端点**组合**而成：

```
[write 端] 用户输入 ──参数化写入──> 存储(DB/Config/Cache/File)
                                       │ （存储里现在躺着用户污染过的数据）
                                       ▼
[read 端]  存储 ──读出(信任、未净化)──> 危险 sink(render / SQL拼 / parse)
```

**关键特征：write 端单独看是 safe 的。**

```js
// write 端 — 参数化写入,本身完全合规,任何单点扫描都判 safe
router.post('/comment', (req, res) => {
  db.query('INSERT INTO comments(body) VALUES(?)', [req.body.body]);
});
// read 端 — 从 DB 读出后未编码直接渲染
router.get('/comments', (req, res) => {
  const rows = db.query('SELECT body FROM comments');   // DB read = STORAGE source
  res.render('list', { items: rows });                   // body 进模板未转义 = XSS sink
});
```

单看 write：参数化，`safe`。单看 read：source 是 DB 读出，若扫描器不认"DB 读出是受污染 source"，就找不到 source→sink 链，`safe`。**只有把 write 的 token（表 `comments`）和 read 的 token（`FROM comments`）join 起来，才知道 read 端渲染的 `body` 其实在 write 端被用户污染过**——这才是漏洞。这就是"组合"。

二阶 SQLi 同理（write 存用户名 → read 把存的名拼进另一条 SQL）；运行时配置二阶同理（`setProperty(userData)` → `getProperty()` → sink）。

---

## 1. 为什么"系统性漏报"（双轨各自的缺口）

二阶在 **TS 版（纯 LLM 单轨）和 PY 重构后的双轨都系统性漏报**，根因是都没有"存储中转"概念。

### 1.1 TS 版（纯 LLM 单轨）

无确定性层，二阶发现全靠 LLM agent 偶尔追到（spec §1.3 用 INJ-09 跨服务二阶印证：靠 inj vuln agent + attack chain agent **双产协同**，非任一单轨独占）。单类 vuln agent 缺"write 端 safe / read 端 vuln"的组合视角。

### 1.2 PY 重构后双轨仍漏（重构前的确定性层缺口）

| 确定性层组件 | 缺口 | 后果 |
|---|---|---|
| `source_rules.yml` | 只有 HTTP source（`@RequestBody` 等），**无 storage-read 风味** | read 端 DB 读出**不识别为 source** |
| `sink_rules.yml` | 无 storage-write sink | write 端**无锚点** |
| `chain_propagator.py` | 只连单跳 source→sink，**无存储中转概念** | write 端参数化被判 `safe` 丢弃（**二阶起点丢**） |

LLM 轨 `vuln-*.txt` 有 cross-service sink 规则但无系统性"存储 write/read 二阶追链"方法论。`attack_chain_assembler._link_key` 的子串 join 只是 **post-vuln 叙事层启发式**（组合两端都已是 finding 的链），不在召回主路径，且二阶 write 端是 safe 的进不了 finding，救不了。

**结论：双轨结构性漏报，不是概率性漏报。**

---

## 2. 核心抽象：三个锚点的真实代码形态

spec 提三个抽象，但**代码里只有一个是独立类**（`storage_models.py`）：

| spec 概念 | 代码真实形态 | 文件 |
|---|---|---|
| **`StorageWritePoint`** | ✅ **真实独立类**（`BaseModel`）。独立类型，**绝不进 `sink_call_sites`**（写 DB 本身不是漏洞，否则单跳轨会把每个 DB 写入误报成漏洞）。携带 `written_expr`（判 write 端是否 user-tainted；**参数化 write 也是合法二阶起点**）。 | `storage_models.py` |
| **`StorageReadPoint`** | ❌ **不是类**，是 `SourcePoint(source_type=STORAGE)` 的复用（flavor=storage）。`detect_storage_reads` 直接产 `SourcePoint`。**这是关键设计**：read 端当普通 source，`chain_propagator` 的 backward 锚定现成支持，零新传播逻辑。 | `storage_detector.py` |
| **`StorageNode`** | ❌ **代码里不存在**（grep 全包零命中），只是 spec 里 `(medium, token)` 的概念标识，体现在 join key 上（`SecondOrderCandidate.storage_token = (medium, token)` 元组）。 | — |

真正需追生命周期的只有两条数据流：**`StorageWritePoint`**（独立字段）和 **STORAGE-flavored `SourcePoint`**（混进 source_points）。

---

## 3. 分析流水线：谁写 / 产物 / 谁用（阶段视角）

按项目编排框架（pre-recon / recon / vuln / report = 顶层 agent/阶段，`AgentName` 枚举印证），二阶锚点跨三个阶段：

```
setup → pre-recon → recon → risk-scoring → vulnerability-analysis → merge → report
          ↑写锚点                                ↑用锚点(双轨并行)        ↑verdict OR
```

### 3.1 谁写：pre-recon agent

**时机**：pre-recon 阶段的 `run_code_index`（`build_code_index`），和 sink/source 检测同批，扫 **entry handler 函数体**（`detect_storage_*` 的 L182/224 都有 `if block.id not in entry_point_ids: continue`）。不在 entry 的 handler 靠 intra-first taint 兜底。

**写者**（都不是多轮 agent）：
- **确定性检测器** `detect_storage_writes/reads`（`storage_detector.py`）：正则规则匹配 `storage_rules.yml`，产硬锚点。
- **LLM hunter** `discover_storage_writes_llm/reads_llm`（`storage_discovery_llm.py`）：轻量 LLM 单次调用（`run_claude_prompt` 结构化输出，**非 agent**），补规则没覆盖的读写点，产 `needs_review` 软锚点。LLM 不可用降级为 `[]`（守"GitNexus 轨确定性兜底"）。

### 3.2 产物是什么：三层

```
① 锚点层(code_index 阶段)
   code_index.json        →  storage_write_points: [StorageWritePoint]   ← write 端独立账本
   parameter_graph.json   →  taint_flows(含 STORAGE source 的链)          ← read 端混在 source_points

② 判定层(vulnerability-analysis 阶段 run_gitnexus_chain_verdict activity)
   second_order_builder 判定 → 2ND-GN-NN findings(InjectionVulnerability)
                              → 写进 {vuln}_gitnexus_queue.json

③ 合并层(merge 阶段)
   {vuln}_gitnexus_queue.json  ∪  {vuln}_exploitation_queue.json  →  verdict OR
```

`2ND-GN-*` 字段（`second_order_builder.py:139`）：`vulnerability_type="second_order_{xss|injection}"`、`source_track="gitnexus"`、`combined_sources`（write+read 两端位置）、`mismatch_reason`（"stored data from {token} reaches {vc} sink without re-validation"）。

### 3.3 谁用：vulnerability-analysis 阶段的 GitNexus 轨（不是 vuln-*.txt）

**这是最易混淆的点。** 同一个 vulnerability-analysis 阶段里跑着**两条平行轨**（`workflows.py` L367-445）：

```
vulnerability-analysis 阶段
├─ ① vuln-*.txt LLM agents (L374 run_vuln_agent, 开轨时并行)
│     ← ❌ 不用 storage 锚点(守铁律B:prompts/vuln-*.txt 零引用确定性产物)
│     ← 自己 grep 追二阶 → {vuln}_exploitation_queue.json
│
├─ ② GitNexus 轨判定 (L432/441)
│     ├─ run_authz_gitnexus_judge (L432) — authz 深度 agent(IDOR)
│     └─ run_gitnexus_chain_verdict (L441) — inj/xss/ssrf + second_order
│           └─ second_order_builder (activities.py:1471):
│              join(storage_writes × STORAGE read_chains by token)
│              + judge_chain_verdict(轻量LLM 判 read 端) + _looks_user_tainted(write 端)
│              ← ✅ 用 storage 锚点(确定性轨判定,非 vuln-*.txt agent)
│
└─ merge (L480): gitnexus_queue ∪ exploitation_queue → verdict OR
```

**`storage_write_points` grep 全工程，唯一消费者是 `second_order_builder`**（join 的 write 端）。STORAGE source（read 端）被 `chain_propagator` 当普通 source 反向锚定产 taint_flows，到 builder 阶段单跳 builder **主动排除 STORAGE 链**（gap A 修复 `2bbd2947`：`source_type != STORAGE`），统一交给 second_order_builder，避免双重计数。

**判定调用方式**：`judge_chain_verdict` 用 `llm_client(prompt, output_format=CHAIN_VERDICT_SCHEMA)`（`chain_verdict.py:272`）—— `run_claude_prompt` 单次结构化输出，**非 agent**。

---

## 4. join 机制：token 边界 + 二分图（不是 BFS）

### 4.1 token 边界（守"误连比漏报更糟"）

`second_order_join.py::is_resolvable_token`：只字面量 token 可静态匹配；`""` / `"unresolvable"` / 含 `+`/`${` 的动态 token → False，归 LLM 轨。

| medium | 字面量 token（GitNexus 可 join） | 动态 token（归 LLM 轨） |
|---|---|---|
| db | SQL 表名 / ORM model 类名 | 动态表名 `${tbl}` |
| config | `getProperty("x.y")` 字面量 key | 变量配置 key |
| cache | `cache.get("user_profile")` | 拼接 key `"u:"+id` |
| file | 路径字面量 | 动态路径 |

`_normalize_token` 注释明说："a wrong guess here would pair unrelated write↔read sites and produce a FALSE second-order finding (误连 > 漏报 更糟)"。

### 4.2 二分图 join（O(|W|×|R|)，刻意不是 BFS）

`extract_second_order_candidates`：write 点集 × read 链集，按 token 配对，同 token 的 N×M 笛卡尔积。**刻意不做跨函数 BFS 深度遍历**（spec §6：BFS 复杂度逼近 LLM 轨，不做）。

**反直觉细节：medium 不参与 join 匹配。** 因为 `SourcePoint`（read 端复用类型）**没有 medium 字段**，只有 `source_type=STORAGE`，read 端介质信息进 source_points 那刻就丢了。spec §3.3 注释："medium cross-check is advisory"。介质隔离靠 **token 命名空间天然分离**（表名 `users` / 配置 key `app.webhook_url` / 缓存 key `user_profile` / 路径 `/tmp/x` 实践中不撞），不靠 medium 字段。

---

## 5. table-name 推断（让 ORM save 能 join SQL read）

join 最大难点：ORM 写入如 `repo.save(u)` **调用点没有表名字面量**，`storage_token="unresolvable"`，join 不到 `FROM users`。`_resolve_write_token` 三级 fallback（**仅同文件**）：

1. **注解**：`@Table(name="users")` / `@TableName` / `@Document` → `{class: table}` map，匹配 receiver 推导的 entity。
2. **命名约定**：`userRepository` → strip `Repository` → `User` → camelCase 转 snake_case + 复数 → `users`（含 person→people 等不规则复数）。
3. **保守保留**：generic receiver（`repo`/`session`/`db`）/ 无上下文 → 保留原 token，**绝不臆造**（under-recall 归 LLM 轨）。

read 侧 `_resolve_read_table`：`FROM/INTO <table>` SQL 提取 → 尾部字面量 → fallback `param_name`。这就是让 `repo.save(u)` 和 `SELECT body FROM comments` join 上的关键——两端归一到规范表名。

---

## 6. 判定模型：write tainted ∧ read vulnerable（核心）

### 6.1 判定分解

`second_order_builder.py::build_second_order_findings`（编排入口 `activities.py:1471`）：

```
1. extract_candidate_chains(pgraph, "xss") + ("injection")   # read 端单跳链(含 STORAGE source)
2. extract_second_order_candidates(writes, read_chains)       # join by (medium, token)
3. 每个 candidate:
     read_verdict  = judge_chain_verdict(read_side_chain)      # 轻量 LLM 判 read 端
     write_tainted = _looks_user_tainted(write.written_expr)   # write 端启发式
     is_vuln = write_tainted and (read_verdict == "vulnerable")
4. 产 InjectionVulnerability(ID="2ND-GN-NN", source_track="gitnexus")
```

### 6.2 为什么把"可控性"判断从 read 端挪到 write 端（机制精髓）

**`chain_verdict` 只判净化，不判 source 外部可控性**。其判定 prompt（`chain_verdict.py:66-86`）只问：① 链路有无 sanitizer；② 净化后有无被重新拼接污染。它**假设 source 是 tainted 的**，根本不判"source 外部可不可控"。

那"判为外部不可控"是谁干的？是 **LLM agent 的主观倾向**——尤其 `getProperty`（config read）。注意 `vuln-xss.txt:171` 对 **DB read** 有硬性规定 "you must assume the data read from the database is untrusted"，但 **config read 没有同等规定**，所以 config 二阶最易被 LLM 判"配置内部可控→safe"漏报。

二阶机制的精髓——**把"外部可控性"判断从 read 端（判不了，看不到 write 端）转移到 write 端（能判）**：

| 判定维度 | 在哪判 | 为什么放这 |
|---|---|---|
| read 端是否未净化 | `chain_verdict`（假设 tainted，只看净化） | read 端能直接看 sink 前代码，判净化是强项 |
| 数据是否外部可控 | write 端 `_looks_user_tainted` + write 必须在 entry handler | read 端**看不到** write 端；write 端能判 |

write 端"外部可控"两层保证：
1. `detect_storage_writes` **只扫 entry handler**（L224）→ write 点天然在对外接口里；
2. `_looks_user_tainted` 排除纯数字/字面量/`config.`前缀/`SCREAMING_SNAKE`常量/enum，余下视为用户数据（保守，只去 false-positive，权威判定在 read 端）。

**典型命中**（用户场景）：对外接口 `setProperty("webhook_url", req.body.url)` → `getProperty("webhook_url")` → SSRF sink。LLM 看 read 端 `getProperty` 易判"配置不可控→safe"漏报；GitNexus 轨⑤用 `(config, "webhook_url")` join write 端污染证据，机械证明配置被对外接口写入 → 推翻误判。

---

## 7. 双轨协同 + verdict OR

二阶也是双轨，守最核心的 OR 不变量（CLAUDE.md §1）：

| | GitNexus 轨 ⑤ | LLM 轨 |
|---|---|---|
| 二阶覆盖 | **字面量 token**（DB/Config/Cache-literal/File） | **动态 token**（动态表名/拼接 key/变量 key） |
| 产出 | `<vuln>_gitnexus_queue.json`（`2ND-GN-*`） | `<vuln>_exploitation_queue.json` |
| 定位 | 关轨兜底（`SUPERNOVA_LLM_TRACK_ENABLED=0` 时仍跑） | 开轨增强 |
| 判定标准 | write tainted ∧ read vulnerable（精确，可解释） | DB read 硬性当 untrusted，read 端未编码即 vulnerable（宽松，宁过报） |

LLM 轨方法论落地（grep 确认）：
- **xss**：`vuln-xss.txt:168-180` Database Read Checkpoint，backward 追到 DB read 即终止，**主动不追 write 端**（L173）。
- **ssrf**：`vuln-ssrf.txt:230` Stored SSRF（trace 到 DB read 的 webhook URL 未净化）。
- **injection**：grep 未命中系统二阶方法论段落。

OR 后两轨判定标准不同（GitNexus ⑤ 要求两端成立，LLM xss 只要求 read 端未编码）→ 取并集，**有意设计**，宁过报不漏报，由 `chain_verdict` / exploit 阶段复核。

---

## 8. 与 attack_chain 的边界（避免重复计数）

spec §3.5 划清分工：

| | 二阶 builder ⑤（vuln 召回阶段） | attack_chain_assembler（post-vuln 组合层） |
|---|---|---|
| 处理什么 | **write 端可 safe** 的真正二阶 | 两端**都已是 finding** 的链叙事 |
| 产出 | `<vuln>_gitnexus_queue.json`，**计入漏洞数** | `attack_chains.json`，**不计漏洞数** |
| 定位 | 召回主路径 | 跨类组合叙事 |

两者产不同 queue、merger 去重、不冲突。

---

## 9. 四介质覆盖与边界

### 9.1 规则覆盖（`storage_rules.yml`）+ 判定统一

四种介质走**完全同一套** detect→join→判定，builder 不分介质。差异只在规则层 + token 来源 + db 需 table 推断：

| 介质 | write 规则 | read 规则 | token 来源 | 需推断? |
|---|---|---|---|---|
| **db** | java-orm-save / python-sqlalchemy-add / go-gorm-write / php-eloquent-create / php-db-table-insert | java-orm-find、raw SQL `FROM/INTO` | 表名 / ORM 类名 | ✅ ORM save 三级推断 |
| **config** | java-setproperty | java-getproperty | 配置 key 字面量 | ❌ |
| **cache** | ts-cache-set / python-cache-set | ts-cache-get | 缓存 key 字面量 | ❌ |
| **file** | ts-writefile | ts-readfile | 路径字面量 | ❌ |

判定 `write_tainted ∧ read_vulnerable` 对四介质完全一样。

### 9.2 覆盖度不均衡（现状，诚实标注）

机制统一，但**硬规则覆盖不均**（由 LLM hunter 软锚点 + LLM 轨补）：
- **db**：write 覆盖 5 语言，但 **read 端只有 Java 的 `java-orm-find`** + raw SQL——Python/Go/PHP/TS 的 ORM find 读规则缺。read 端漏 = join 不上，是 db 二阶瓶颈。
- **config**：只 Java。
- **cache**：TS 有 write+read，Python **只有 write 缺 read**。
- **file**：只 TS。

**read 端普遍窄于 write 端**——补 read 端规则是提二阶召回的关键杠杆。

---

## 10. 覆盖的漏洞与场景

### 漏洞类型

| 漏洞 | GitNexus ⑤ | LLM 轨 | 产物 ID |
|---|:--:|:--:|---|
| **stored XSS** | ✅（builder 抽 xss class） | ✅（vuln-xss Database Read Checkpoint） | `2ND-GN-*` / exploitation |
| **二阶 SQLi** | ✅（builder 抽 injection class） | ⚠️（方法论未系统落地） | `2ND-GN-*` / exploitation |
| **Stored SSRF** | ❌（builder 不抽 ssrf class） | ✅（vuln-ssrf:230） | exploitation |

注意：`second_order_builder` 只抽 **xss + injection** 两个 class（`second_order_builder.py:103-107`），SSRF 的 stored 形态 GitNexus 轨不覆盖。

### 命中场景（GitNexus ⑤ 字面量 token 可 join 的同服务二阶）

- **db**：评论写库→读库未编码渲染（stored XSS）；写用户名→读出拼 SQL（二阶 SQLi）
- **config**：对外接口 `setProperty(userData)`→`getProperty()`→sink（运行时配置二阶）
- **cache**：`cache.set` 写→`cache.get` 读→sink
- **file**：`writeFile` 写→`readFile` 读→deserialize/render

### 不命中（边界）

- **跨服务二阶**（INJ-09 跨 HTTP 进程）：归 LLM 轨，spec §2 非目标（存储 token 跨进程不可静态推导）。
- **动态 token**（动态表名 `${tbl}`、拼接缓存 key）：归 LLM 轨。
- **静态配置**（无写接口，确实外部不可控）：不做，且判 safe 正确（spec §3.2：配置介质限定运行时可写配置）。

---

## 11. 守的铁律

- **铁律 B**（CLAUDE.md §1）：LLM 轨二阶方法论是 LLM **自主 grep**，**不吃 GitNexus 确定性产物**。守 `test_static_dataflow_hints_decoupling.py`。
- LLM hunter 守 entry 约束 + `needs_review` + `chain_verdict` 复核。
- `externally_exploitable`（可达性标签）不被 verdict 覆写。

---

## 12. 验证状态

- **8 task TDD 全完成，24/24 测试绿**（DB/Config/Cache/File 四介质 fixture、token 边界、join 正确性、verdict 复用、守铁律）。
- **集成 gap 已修**（commit `2bbd2947`）：`extract_candidate_chains` 按 sink 路由不看 source_type 致 STORAGE 链被单跳 builder 抽走 + 二阶 builder 双重 emit；加 `source_type != STORAGE` 过滤。
- **builder 已接进编排**：`run_gitnexus_chain_verdict`（`activities.py:1471`）调 `build_second_order_findings`，产 `2ND-GN-*` 进 `{vuln}_gitnexus_queue.json`，参与 merge。
- **真机冒烟待跑**：sentinel_dashboard 关轨重扫验 `2ND-GN` 非空、NodeGoat stored XSS fixture 回归——目前是"测试绿 + 真机待验"状态。

---

## 13. 关键文件清单

| 文件 | 职责 |
|---|---|
| `code_index/storage_models.py` | `StorageWritePoint` 真类定义（`StorageReadPoint` 复用 SourcePoint，`StorageNode` 仅概念） |
| `code_index/storage_detector.py` | `detect_storage_writes/reads`（规则匹配，扫 entry handler） |
| `code_index/data/storage_rules.yml` | 四介质 read/write 规则库 |
| `code_index/storage_discovery_llm.py` | `discover_storage_*_llm`（轻量 LLM hunter 补召回） |
| `code_index/second_order_join.py` | `extract_second_order_candidates`（二分图 token join + table 推断 + `_resolve_write_token`） |
| `code_index/vuln_chain_builders/second_order_builder.py` | `build_second_order_findings`（join + 判定，产 `2ND-GN-*`） |
| `code_index/chain_verdict.py` | `judge_chain_verdict`（read 端单跳轻量 LLM 判定，判净化不判可控性） |
| `code_index/vuln_chain_builders/{injection,xss,ssrf}_builder.py` | 单跳 builder，**主动排除 STORAGE 链**（gap A） |
| `whitebox/pipeline/activities.py:1471` | `run_gitnexus_chain_verdict` 编排 second_order_builder |
| `whitebox/pipeline/workflows.py:218/441` | pre-recon 写锚点 / vulnerability-analysis 阶段 GitNexus 轨用 |
| `prompts/vuln-xss.txt:168` / `vuln-ssrf.txt:230` | LLM 轨二阶方法论（自主 grep，不吃确定性产物） |

---

## 一句话

二阶存储中转是"组合漏洞"——write 端参数化 safe + read 端信任存储未净化到 sink，单点扫描两边都判 safe 而漏报。PY 的修复：**pre-recon agent**（`run_code_index` 内确定性检测器 + LLM hunter）写 `StorageWritePoint`（独立账本，非 sink）+ STORAGE source（复用 SourcePoint），**vulnerability-analysis 阶段的 GitNexus 轨** `second_order_builder`（非 vuln-*.txt agent）按 `(medium, token)` 二分图字面量 join + `write_tainted ∧ read_vulnerable` 判定，**把可控性判断从 read 端（判不了）挪到 write 端（能判）**，再与 LLM 轨（vuln agent 自主追动态 token 二阶）verdict OR。关键取舍：**误连比漏报更糟**（字面量才 join、动态归 LLM、绝不臆造表名、medium 不参与匹配靠命名空间分离）。
