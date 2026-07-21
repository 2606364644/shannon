# 存储中转二阶漏洞召回:GitNexus 轨确定性 join + LLM 轨二阶方法论(双轨)

> 状态:design(待 plan)。分支 `feat/fork-py`。
> 起源:2026-07-21 会话,"补同服务二阶"(stored XSS / 二阶 SQLi / 运行时配置二阶)brainstorming。
> 关系:`code-index-deterministic-asset-layer` spec 的**子项⑤**(GitNexus 轨部分)+ LLM 轨 `vuln-*.txt` 二阶方法论。

---

## 1. 背景与根因

### 1.1 现象

同服务二阶漏洞(stored XSS、二阶 SQLi、运行时配置二阶)在双轨都**系统性漏报**。典型形态:接口 A 把用户数据写入存储(write 端,常参数化、本身无害),接口 B 从同一份存储读出(read 端)并信任该数据,直接带到危险 sink(render / query / parse)未净化。

### 1.2 根因(两条轨各自的缺口)

**GitNexus 轨**——确定性层无"存储中转"概念:
- `source_rules.yml` 无 storage-read source 风味(只 `@RequestBody`/`@RequestParam`/`@PathVariable`)
- `sink_rules.yml` 无 storage-write sink(`createNativeQuery` 是查询 sink,非写入语义;`ts-document-write` 是文件写,未入二阶体系)
- `chain_propagator.py` 无存储中转/二阶概念,只连单跳 source→sink

后果:write 端参数化被判 safe 丢弃(二阶起点丢);read 端 source(DB/config/cache 读出)不识别。`attack_chain_assembler._link_key` 的 crude 子串 join 只是 post-vuln 叙事层启发式,不在召回主路径,且依赖两端都已是 finding。

**LLM 轨**——`vuln-*.txt` 有 cross-service sink 规则(`vuln-injection.txt:183`)但**无系统性"存储 write/read 二阶追链"方法论**。二阶发现靠 agent 偶尔追到,非系统召回。

### 1.3 INJ-09 双产证据(原始 TS 版,印证架构判断)

原始版 sentinel_dashboard 扫描,INJ-09 跨服务二阶 fastjson **双产**:
- **inj vuln agent** 产 `INJ-VULN-09`(InsecureDeserialization, "cross-service injection is still an injection" per methodology)→ `injection_exploitation_queue.json`
- **attack chain agent** 产 `llm-chain-2`(POST /registry/machine → GET rule-fetch → fastjson parse 两步链)→ `attack_chains_llm_queue.json`

→ 证明二阶是 **vuln agent(单类)+ attack chain(跨类组合)协同**,非任一单轨可独占。

> 注:INJ-09 是**跨服务**二阶(跨 HTTP 进程),属本 spec **非目标**(归开轨,见 §2/§6);但它印证了"二阶需双轨协同"的架构判断。

---

## 2. 目标

让"同服务二阶"成为双轨各自的**系统能力**,verdict OR:

1. **GitNexus 轨(子项⑤)**:确定性存储 token join + `chain_verdict` 二阶判定。守图锚定,覆盖**字面量 token** 二阶。关轨兜底。
2. **LLM 轨**:`vuln-*.txt` 加二阶方法论,Task Agent subagent 自主追。覆盖**动态 token** 二阶。开轨增强。
3. **双轨二阶 verdict OR**:GitNexus⑤(字面量 token)+ LLM(动态 token)互补。

**非目标**:
- **跨服务二阶(INJ-09 跨 HTTP 进程)**:存储 token 跨进程不可静态推导,仍归开轨。
- **不动 attack chain agent**(只理清分工边界)。
- **不把 attack chain 并入 vuln agent**(时序+跨类视角不允许,见 §6)。

---

## 3. 架构

### 3.1 核心抽象(3 个新锚点)

**`StorageNode`** —— 存储中转节点,由 `(medium, token)` 标识。`medium ∈ {db, config, cache, file}`。join 枢纽,不对应具体代码位置。

**`StorageWritePoint`** —— 数据流入存储的位置(ORM save/insert/update、SQL INSERT/UPDATE、`setProperty`/配置写、`cache.set/put`、文件写)。
- **关键:非危险 sink。** 写 DB 本身不是漏洞。是**独立锚点类型,不进 `sink_call_sites`**——避免单跳轨把 DB 写入误报成漏洞。
- 携带 `written_expr`(判断 write 端是否 user-tainted;**参数化 write 也是合法二阶起点**)。

**`StorageReadPoint`** —— 数据从存储流出的位置(ORM find/select、SQL SELECT 结果、`@Value`/`getProperty`/`@ConfigurationProperties`、`cache.get`、文件读)。
- **新 source 风味**(`flavor=storage`),进 `source_points`。挂 code-index spec 子项② 风味机制。
- 携带 `read_var`(读出变量名)。
- **可行性已验证**:`chain_propagator.propagate_backward_across_chains` 用 `SourcePoint` 双向锚定(`_source_points_matching` 按 `SourcePoint.expression` substring 匹配),天然从 sink 反向回溯锚定到 `read_var` 产 `TaintFlow(read_var→sink)`;`extract_candidate_chains` 按 sink 路由(`_route_for` 只看 `sink_slot`/`sink_category`,**不看 source 风味**)抽出 → `judge_chain_verdict` 判定。**read 端复用单跳判定链路成立**(见 §3.3)。

### 3.2 token 边界(守图锚定底线)

只抽**字面量** token。动态 token → 标 `unresolvable`,不 join(保守漏报,归 LLM 轨)。

| medium | token 来源 | 字面量可 join | 动态/拼接 → LLM 轨 |
|---|---|:--:|---|
| **db** | SQL 表名 / ORM model 类名 | ✅ | 动态表名 `${tbl}` |
| **config** | `@Value("${x.y}")` / `getProperty("x.y")` 字面量 key(**运行时可写配置**) | ✅ | 变量配置 key |
| **cache** | 字面量 key `cache.get("user_profile")` | ✅ | 拼接 key `"u:"+id` |
| **file** | 路径字面量 | ✅ | 动态路径 |

> 配置介质限定**运行时可写配置**(`setProperty` → `getProperty` → sink)。静态配置审计(`auth.enabled=false`)不在本 spec。

### 3.3 GitNexus 轨子项⑤(确定性层)

**数据流**(`code_index/__init__.py` 编排,与 code-index spec 子项① 并行化协调):

```
detect_sinks ∥ detect_sources ∥ detect_storage_writes ∥ detect_storage_reads   (硬规则,4 路并行)
discover_*_llm   (4 路 LLM 探测器,均守 entry 约束 + needs_review)
        ↓
chain_propagator 连边:
   HTTP param → sink          (单跳,现有)
   StorageReadPoint(read_var) → sink   (read 端当普通 SourcePoint,backward 天然锚定)
        ↓
extract_second_order_candidates(writes, reads) → 按 (medium, token) join → SecondOrderCandidate[]
        ↓
builders: 单跳 build_{injection,xss,ssrf}_findings  +  新增 build_second_order_findings
        ↓
<vuln>_gitnexus_queue.json   (单跳 findings + second_order findings,带标记)
```

**二阶判定(关键简化——read 端复用单跳)**:
- `SecondOrderCandidate { write_point, storage_token, read_side_chain(单跳 CandidateChain) }`
- read 端 verdict:**复用 `judge_chain_verdict(read_side_chain)`**
- write 端确认:新增轻量判定 `written_expr` 是否 user-controlled
- **二阶 verdict = (write tainted) ∧ (read 端单跳 vulnerable)**

**存储读写点识别**:规则 + LLM 探测器对称(对称 code-index spec 子项③ sink 探测器 / 子项② source 探测器)。LLM 探测器守 entry 约束,产 `needs_review` 软锚点,`chain_verdict` 复核。

**join = 二部图,不是 BFS**:write 点集 × read 点集,按 `(medium, token)` 配对。O(|W|×|R|) token 字面量匹配,**无跨函数深度遍历**——不撞 code-index spec 第 6 节「不做宽版 BFS」边界。

### 3.4 LLM 轨 `vuln-*.txt` 二阶方法论

给 `vuln-{injection,xss,ssrf}.txt` 审查并补强二阶追链方法论(**守铁律——LLM 自主 grep,不吃 GitNexus 确定性产物**):

- 系统性找存储 **write** 点(grep ORM save/insert、`setProperty`、`cache.set`)+ 抽 token
- 找存储 **read** 点(grep find/select、`getProperty`、`cache.get`)+ 抽 token
- 按 token 关联两端 + 追 read 端到危险 sink
- **delegate 给 Task Agent subagent 做**(对称现有 vuln prompt 的 Task Agent 追链,`vuln-injection.txt:98-104` MANDATORY)
- 覆盖**动态 token** 二阶(GitNexus 轨静态 join 不到的)

> xss 轨 `xss_builder` 已有部分 Stored synthesis(`chain_verdict.py` 注释提及);plan 阶段审查 xss 现有 stored 处理,避免方法论重复,重点补 inj/ssrf。

产出进 `<vuln>_exploitation_queue.json`(LLM 产物),与 GitNexus⑤ verdict OR。

### 3.5 双轨协同 + attack chain 分工

| | GitNexus⑤ | LLM 轨 | attack chain(不动) |
|---|---|---|---|
| 二阶覆盖 | 字面量 token | 动态 token | — |
| 产出 | `<vuln>_gitnexus_queue.json`(second_order) | `<vuln>_exploitation_queue.json` | `attack_chains.json`(跨类叙事) |
| 定位 | 关轨兜底 | 开轨增强 | post-vuln 跨类组合 |

**⑤ vs `attack_chain_assembler` 分工**(避免重叠):
- ⑤(vuln 召回阶段):发现 **write 端可 safe** 的真正二阶,产 finding **计入漏洞数**。
- assembler(post-vuln 组合层):组合**两端都已是 finding** 的链叙事,产 `attack_chains.json`,不计漏洞数。
- 两者产不同 queue,merger 去重,不冲突。⑤落地后 assembler 的 stored XSS 组合仍保留(它处理两端-finding 情况)。

---

## 4. 不变量 / 铁律边界

- **铁律(CLAUDE.md §1)**:LLM 轨二阶方法论是 LLM **自主 grep**(含义 A),**不吃 GitNexus 确定性产物**(含义 B 禁止——会把 LLM 轨绑定确定性层、破坏独立性)。守 `test_static_dataflow_hints_decoupling.py`。
- LLM 探测器守 entry 约束 + `needs_review` + `chain_verdict` 复核(同 code-index spec 子项③)。
- `externally_exploitable`(可达性标签)不被 verdict 覆写(CLAUDE.md §1)。
- 全在各自轨,**不改 attack chain agent**。

---

## 5. 测试(TDD)

- **DB 介质**:stored XSS fixture(参数化 write comment → read 未编码 render)、二阶 SQLi fixture(write name → read 拼接 query)
- **Config 介质**:运行时配置二阶(`setProperty(userData)` → `getProperty` → sink)
- **Cache/File**:字面量 token join 通过
- **token 边界**:字面量 join ✓;动态/拼接 → `unresolvable` 不 join ✓
- **join 正确性**:同 `(medium, token)` 连、不同不连
- **verdict 复用**:read 端单跳 vulnerable + write tainted → 二阶 vulnerable;read 端 safe → 二阶 safe
- **LLM 二阶方法论**:NodeGoat stored XSS fixture(agent 系统追二阶)
- **守铁律**:decoupling 测试绿;⑤与 assembler 不冲突(不同 queue)

---

## 6. 不做(YAGNI)

- **跨服务二阶(INJ-09 跨 HTTP)**:存储 token 跨进程不可静态推导,归开轨。
- **动态 token 确定性 join**:动态表名 / 拼接缓存 key / 变量配置 key → `unresolvable`,归 LLM 轨。
- **attack chain 并入 vuln agent**:时序(attack chain 须等所有 vuln 跑完、消费全类 findings)+ 视角(跨类组合 vs 类内深挖)不允许。attack chain 保持独立,专注跨类叙事(INJ-09 式两步链、IDOR 序列、业务逻辑链)。
- **BFS 跨函数追链**:code-index spec 第 6 节已排除(复杂度逼近 LLM 轨)。本 spec 的二部图 token join **不是 BFS**,不撞该边界。

---

## 7. 覆盖评估

| 二阶类型 | GitNexus⑤ | LLM 轨 | 开轨 |
|---|:--:|:--:|:--:|
| 字面量 token(DB / Config / Cache-literal / File) | ✅ | ✅ | ✅ |
| 动态 token(动态表名 / 拼接缓存 key / 变量配置 key) | ❌ | ✅ | ✅ |
| 跨服务二阶(INJ-09 跨 HTTP) | ❌ | ⚠️(跨服务非存储中转) | ✅ |

- **关轨**:GitNexus⑤ 覆盖字面量 token 二阶(关轨兜底,从"系统性漏报"降到"仅动态/跨服务漏报")。
- **开轨**:LLM 轨补动态 token;跨服务二阶仍靠 LLM 轨现有 cross-service 能力。
- 双轨 OR。

---

## 8. 关键文件清单

> 文件名为建议,plan 阶段确定。

### 必改(GitNexus 轨⑤)
- `code_index/storage_detector.py`(新)— StorageWritePoint/ReadPoint 规则识别
- `code_index/storage_discovery_llm.py`(新)— 存储读写点 LLM 探测器(对称 source/sink 探测器)
- `code_index/second_order_join.py`(新)— token 抽取 + 二部图 join + `SecondOrderCandidate`
- `code_index/parameter_models.py` / `models.py` — `StorageReadPoint` 进 `SourcePoint` 体系(`flavor=storage`)
- `code_index/chain_propagator.py` — 接受 `StorageReadPoint` 作为 source(backward 锚定,已验证天然支持,仅需 SourcePoint 风味打通)
- `code_index/chain_verdict.py` — 二阶判定(write tainted 确认;read 端复用 `judge_chain_verdict`)
- `code_index/vuln_chain_builders/second_order_builder.py`(新)— `build_second_order_findings`
- `code_index/__init__.py` — 编排(存储识别并行 + join + builder)

### 必改(LLM 轨)
- `prompts/vuln-injection.txt` / `vuln-xss.txt` / `vuln-ssrf.txt` — 二阶方法论段落(审查 xss 现有 stored 处理,重点补 inj/ssrf)

### 必读(契约)
- `code_index/chain_verdict.py`(`extract_candidate_chains` 按 sink 路由 + `judge_chain_verdict`)
- `code_index/chain_propagator.py`(`_source_points_matching` backward 锚定 + `propagate_backward_across_chains`)
- `code_index/attack_chain_assembler.py`(分工边界)
- `code_index/data/source_rules.yml` / `sink_rules.yml`(规则覆盖现状)

### 对照参考
- `docs/superpowers/specs/2026-07-21-code-index-deterministic-asset-layer-design.md`(子项②③ 探测器/风味模板)

---

## 9. 后续

- 本 spec 为 design,完成后写 plan(拆 GitNexus⑤ task 序列 + LLM prompt task,两轨可并行)。
- 真机验收:sentinel_dashboard 关轨重扫,stored XSS / 二阶 SQLi 候选非空;NodeGoat stored XSS fixture 回归。
