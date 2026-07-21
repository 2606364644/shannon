# sink 硬规则增强设计:receiver_pattern 失配修复 + Java 全类别补齐

> 状态:design(待 plan)。分支 `feat/fork-py`。
> 起源:2026-07-21 会话,关轨扫 `sentinel_dashboard` 只出 auth/authz、inj/xss/ssrf 全空的根因深挖。
> 分工:其他终端负责 **sink 探测器补召回**(entry-driven LLM 探测,见 `2026-07-21-code-index-deterministic-asset-layer-design.md`);本 spec 负责 **硬规则增强**(全仓确定性 + LLM 兜底)。两者互补,不重叠。

---

## 1. 背景与根因

### 1.1 与 sink 探测器的关系(分工,不重叠)

sink 探测器(`discover_sinks_by_entry`)和硬规则(`data/sink_rules.yml`)是**互补**关系,不是替代:

| | sink 探测器 | 硬规则 |
|---|---|---|
| 范围 | 仅 entry handler 函数体 | 全仓所有函数 |
| 覆盖对象 | 框架特有 sink(fastjson.parseObject) | 经典高频 sink(execute/exec/readObject) |
| 依赖 LLM | 是(stub/超时即空) | 否 |
| 成本 | 每函数一次 LLM(贵) | 零 token |
| 置信度 | 软 sink `needs_review` | 高置信直接进链 |

探测器落地后,硬规则仍有**三个不可替代的价值**,故仍要增强:

1. **非 entry 函数的 sink**(最高优先级):探测器只扫 entry handler(`collect_entry_handler_blocks` 只收 `entry_point_ids` 里的函数)。但原始版漏洞里占大头的 SSRF(`SentinelApiClient.executeCommand`→`httpClient.execute`、`PprofService.buildPprofUrl`)全在 client/service **非 entry 层**——探测器扫不到,只能靠硬规则全仓识别 + call graph 追链。
2. **LLM 不可用时的确定性兜底**(CLAUDE.md 铁律:GitNexus 轨 = 可靠兜底)。LLM stub/超时时探测器全空,只有硬规则能产 sink。
3. **零成本高频经典 sink**:硬规则零 token,把 LLM 成本省给探测器处理框架特有的。

### 1.2 硬规则 0 命中根因(已确认)

`_rule_matches`(`sink_detector.py:224-232`)实现:

```python
def _rule_matches(rule, receiver):
    if rule.receiver_pattern is None:
        return receiver is None   # ★ null pattern 只匹配「无 receiver 的裸调用」
    if receiver is None:
        return False
    return bool(rule.receiver_pattern.match(receiver))
```

Java 是静态类型语言,method call 几乎都是 `instance.method()`,receiver **恒为实例变量名**(`httpClient`/`stmt`/`em`)或类名(`JSON` 静态调用),**绝不可能是裸调用**。但现有 8 条 Java 规则的 `receiver_pattern`:

- **6 条 `rp=null`**(`java-stmt-execute`/`executequery`/`jpa-createnativequery`/`objectinput-readobject` 等)→ 只匹配裸调用,Java 里根本不存在 → 全失配
- **2 条 `rp=^(类型名)$`**(`java-httpclient-send` `^(HttpURLConnection|OkHttpClient|HttpClient)$`、`java-runtime-exec` `^(Runtime|getRuntime)$`)→ 只匹配 receiver 恰好是类型名,但 Java receiver 是实例变量名(`httpClient` 小写)或整链(`Runtime.getRuntime()`)→ 失配

**实测**(sentinel_dashboard `code_index.json` 的 blocks):`execute()` 的 receiver 全是 `httpClient`(实例变量),`parseObject()` 的 receiver 全是 `JSON`(类名静态调用)。8 条规则无一能匹配 → 硬规则 sink 命中 **0**。

对比:Python `cursor.execute(sql)` 能命中,纯粹因为 Python 惯例变量名恰好叫 `cursor`/`conn`/`db`,匹配 `^(cursor|cnx|conn|db|database)$`。Java 没有这种惯例收敛。

**关键**:这是**纯规则层**问题——`detect_sinks`(`__init__.py:106`)在跑、Java parser(`java_parser.py:88` `iter_calls` 切 `method_invocation` + `destructure_call` 取 name/object 字段)切 call 正确。改 `receiver_pattern` 即可命中。

### 1.3 Java 的设计约束

receiver 是任意实例变量名,**不能像 Python 靠惯例命名收窄**。要么:
- rp=`.+`(任意非空 receiver)+ `needs_review_default: true`——靠 callee 名消歧,LLM 复核滤误报(有 `ts-orm-model-query` 先例:`receiver_pattern: ".+"` + `needs_review`)
- 针对类名(`JSON`/`Runtime`)写 rp

---

## 2. 目标

让硬规则对 Java(及排查到的别语言)**真实代码生效**:修 `receiver_pattern` 失配 + 补 Java 全类别缺失 sink。配合 sink 探测器,使关轨模式下 GitNexus 兜底从「灾难性归零」降到「可控」——补回 fastjson 等单点 sink(确定性层也能产,不依赖探测器 LLM),并覆盖非 entry 层的经典 sink。

**非目标**(见 §7):不追求关轨追平原始 TS 全覆盖(业务逻辑缺陷/跨服务二阶结构性覆盖不了,仍需开轨);不碰 LLM 轨 prompt(铁律)。

---

## 3. Architecture:receiver_pattern 三分原则

所有 Java 规则按 callee 独特度分三类处理:

| callee 类型 | 处理 | 判定标准 |
|---|---|---|
| **独特** | rp=`.+` + `needs_review` | callee 名几乎只用于该 sink(`executeQuery`/`parseObject`/`readObject`/`getForObject`/`sendRedirect`/`openConnection`) |
| **通用但可配类名** | rp=`^(类名)$` 或拆语义规则 | callee 通用,但 receiver 是固定类(execute→配 httpClient 语义规则) |
| **太通用无法消歧** | **不加硬规则,交探测器** | `start`/`update` 裸/`get`/`post` 裸/`connect` 裸——加了误报爆炸 |

先例:`ts-orm-model-query`(`callee: query`、`receiver_pattern: ".+"`、`needs_review_default: true`)——注释明说「receiver `.+` = 任意非空 receiver,静态精度不足,needs_review 交 LLM 复核」。

---

## 4. 规则清单

### 4.1 改:现有 8 条 Java 规则修 `receiver_pattern`

| rule_id | callee | 现 rp | 改后 | 理由 |
|---|---|---|---|---|
| `java-stmt-executequery` | executeQuery | null | `.+`+review | 独特,几乎只 SQL |
| `java-jpa-createnativequery` | createNativeQuery | null | `.+`+review | 独特 |
| `java-objectinput-readobject` | readObject | null | `.+`+review | 独特 |
| `java-runtime-exec` | exec | `^(Runtime\|getRuntime)$` | `.+`+review | receiver 实为整链 `Runtime.getRuntime()`,正则匹配不到;exec 较独特 |
| `java-resttemplate-getforobject` | getForObject | `^(restTemplate\|RestTemplate\|webClient\|WebClient)$` | `.+`+review | getForObject 独特 |
| `java-resttemplate-exchange` | exchange | 同上 | `.+`+review | 主要 RestTemplate/WebClient |
| `java-httpclient-send` | send | `^(HttpURLConnection\|OkHttpClient\|HttpClient)$` | `.+`+review | receiver 是实例变量,非类型名 |
| `java-stmt-execute` | execute | null | `.+`+review(**保留,见 4.3**) | 不删——DDL 场景只有 execute 能覆盖 |

### 4.2 加:Java 全类别缺失 sink(独特 callee + `.+` + review)

**Deserialization**(复现原始版 INJ-01~03/09 的 fastjson):
- `java-fastjson-parseobject`(parseObject, `.+`+review, deser)—— **核心,直接复现 INJ-01 的 `JSON.parseObject`**
- `java-fastjson-parsearray`(parseArray, `.+`+review, deser)
- `java-jackson-readvalue`(readValue, `.+`+review, deser)—— Jackson `enableDefaultTyping`

**SSRF**(覆盖原始版 INJ-04~08 / SSRF-02~10):
- `java-httpclient-execute`(execute, `.+`+review, **ssrf**)—— **核心,接住 `httpClient.execute`**
- `java-resttemplate-postforentity`(postForEntity, `.+`+review, ssrf)
- `java-url-openconnection`(openConnection, `.+`+review, ssrf)—— `URL.openConnection`

**SQL**:
- `java-jpa-createquery`(createQuery, `.+`+review, sql)—— HQL/JPQL
- `java-jdbctemplate-query`(query, `.+`+review, sql)—— JdbcTemplate.query
- `java-stmt-executeupdate`(executeUpdate, `.+`+review, sql)—— JDBC DML(INSERT/UPDATE/DELETE),executeUpdate 独特

**Redirect**:
- `java-response-sendredirect`(sendRedirect, `.+`+review, redirect)—— `HttpServletResponse.sendRedirect`

### 4.3 execute 双语义规则(关键决策:不删,不漏 DDL)

`execute` 一个 callee 横跨两类 sink:`Statement.execute(sql)`(SQL)与 `httpClient.execute(request)`(SSRF),callee 名无法区分。处置:

| 规则 | callee | category | rp | 覆盖 |
|---|---|---|---|---|
| `java-stmt-execute`(保留) | execute | **sql** | `.+`+review | `Statement.execute` 的 DDL/DML/SELECT 全覆盖 |
| `java-httpclient-execute`(新增) | execute | **ssrf** | `.+`+review | `httpClient.execute` |

`httpClient.execute` 会被两条规则**双重命中**(一条判 SQL、一条判 SSRF)——callee 同名无法避免。靠 `needs_review` 机制消化:

- 两条都产软 sink(`needs_review=True`)→ 经 `chain_verdict` 轻量 LLM 复核
- LLM 看 receiver=`httpClient` + 参数是 `HttpGet`/`request` → **否决**「SQL 注入」候选链、**肯定**「SSRF」候选链
- `detect_sinks` 本就支持同 callee 多规则多 SinkCallSite(intentional,`sink_detector.py:152` 注释明说),双重命中是设计内行为

**覆盖与代价**:

| 场景 | 结果 |
|---|---|
| `Statement.execute("DROP TABLE...")` (DDL) | `java-stmt-execute` 命中 → SQL 链 → 复核肯定 ✅ **不漏** |
| `Statement.execute("UPDATE...")` (DML) | 同上 ✅(另有 `java-stmt-executeupdate` 双保险) |
| `httpClient.execute(req)` (SSRF) | 双命中 → SQL 链被复核否决、SSRF 链肯定 ✅ 正确,多一次复核成本 |
| `future.execute()` (非 sink) | 双命中 → 复核否决两条 ✅ |

**净代价**:`httpClient.execute` 多触发一次 `chain_verdict` LLM 复核(可接受,正是 `needs_review` 用途)。**DDL/DML/SELECT 全覆盖,不漏报。**

> 注:`httpClient.execute` 双命中后 `chain_verdict` 否决 SQL 那条的行为是下游模块职责,**本 spec 不改 `chain_verdict`**;作为集成验证点(plan 末尾标注)。

### 4.4 不加(YAGNI,太通用无法消歧)

- `ProcessBuilder.start`(start 极通用,Thread.start/进程 start 满天飞)
- `Files.write`(write 通用,需 `Files` 类名——可选 follow-up `java-files-write` rp=`^(Files)$`)
- 裸 `get`/`post`/`connect`(Java 里到处是)→ 交探测器

---

## 5. 别语言排查(C 范围,三处小修)

### 5.1 php(中等)— parser 层 lstrip `$`

**根因**:`php_parser.destructure_call` 的 `member_call_expression` 分支 receiver **不 lstrip `$`**(name lstrip 了,receiver 没)。`$mysqli->query` → callee=`query`(已 lstrip)、receiver=`$mysqli`(带 `$`)→ 规则 `^(mysqli|pdo|db|DB)$` 不匹配。

**修法**:`php_parser.destructure_call` 的 `member_call_expression` / `scoped_call_expression` 分支 receiver 也 `lstrip("$")`(与 name 一致)。lstrip 后 `$mysqli`→`mysqli`→匹配。

**安全性**:php typed-rp 全不带 `$`(`php-mysqli-query` `^(mysqli|pdo|db|DB)$`、`php-db-select-static` `^(DB)$`、`php-db-raw` `^(DB)$`),lstrip 后才匹配,**无规则依赖 receiver 带 `$`**。影响 3 条 typed-rp。这是 receiver 与 name 不一致的 bug 修正,非行为变更。

### 5.2 go(极低)

`go-db-query`(rp=null)失配 → 改 rp=`.+`+review(覆盖 `db.Query`)。其余 go 规则惯例命名匹配(`db.Query`→`^(db|gorm|DB)$`✓、`http.Get`→`^(http|net)$`✓),不动。

### 5.3 ts(极低)

`ts-child-process-exec`(rp=null,receiver=`child_process`)→ 改 rp=`^(child_process|cp)$`(惯例 import 名)。其余 ts null-rp(`fetch`/`eval`/`innerHTML`/`redirect`)是真实裸调用,不动。

---

## 6. 测试策略

测试文件:`tests/code_index/test_sink_detector.py`(主)+ `tests/code_index/test_rule_loader.py`(fail-fast 加载)。

**每条新增/修改规则**:构造对应语言代码片段 → 对应 parser 切 block → `detect_sinks` → 断言命中(category / rule_id / needs_review)。

**关键复现测试**:

| 测试 | 代码 | 断言 |
|---|---|---|
| **fastjson 复现** | `JSON.parseObject(payload)`(ClusterConfigController 片段) | `java-fastjson-parseobject` 命中(闭环原始版 INJ-01) |
| **SSRF execute 双命中** | `httpClient.execute(request)` | `java-httpclient-execute`(ssrf) + `java-stmt-execute`(sql)各一个 SinkCallSite |
| **DDL 不漏** | `stmt.execute("DROP TABLE x")` | `java-stmt-execute` 命中 |
| **php `$` 修正** | `$mysqli->query($sql)` | `php-mysqli-query` 命中 |
| **receiver 实例变量** | `myDb.executeQuery(sql)`(任意变量名) | `java-stmt-executequery` 命中(证明不依赖惯例命名) |

**回归**:守 `packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`(铁律)+ `packages/whitebox/tests/test_workflow_gitnexus_failfast.py` + 现有 `test_sink_detector.py` / `test_rule_loader.py` 全绿。

> 测试陷阱(CLAUDE.md §3):只跑本 spec 改动的测试文件,勿广跑全套。

---

## 7. scope 边界 / 非目标

**不做**:
- ❌ 不加太通用 callee(`start`/`update` 裸/`get`/`post` 裸/`connect` 裸)→ 交探测器
- ❌ 不碰 LLM 轨 prompt(`vuln-*.txt`)——铁律:确定性产物不喂 LLM 轨
- ❌ 不改 `_rule_matches` 跨语言语义(策略「Java 特殊化 null」已否决:破坏 null 的跨语言一致性)
- ❌ 不改 `detect_sinks` / `sink_detector.py` 核心匹配逻辑(只改 `data/sink_rules.yml` + `php_parser.py` lstrip)
- ❌ 不改 `chain_verdict`(双命中的复核去重是下游职责,本 spec 只保证产正确的 SinkCallSite)

**非目标**:不追求关轨追平原始 TS 全覆盖(业务逻辑缺陷如 registry poisoning、跨服务二阶 fastjson 反序列化——结构性覆盖不了,仍需开轨)。本 spec 只把硬规则层的漏报从「8 条全 0 命中」修到「Java 真实 receiver 能命中」。

---

## 8. 文件改动清单

| 文件 | 改动 |
|---|---|
| `packages/core/src/shannon_core/code_index/data/sink_rules.yml` | 改 8 条 Java rp + 加 10 条 Java(§4.2:Deser 3 / SSRF 3 / SQL 3 / Redirect 1)+ 改 `go-db-query`/`ts-child-process-exec` |
| `packages/core/src/shannon_core/code_index/parsers/php_parser.py` | `member_call`/`scoped_call` receiver `lstrip("$")` |
| `packages/core/tests/code_index/test_sink_detector.py` | 加规则命中测试(含 fastjson 复现 / execute 双命中 / DDL / php `$`) |
| `packages/core/tests/code_index/test_rule_loader.py` | 新规则 fail-fast 加载断言 |

---

## 9. 风险与验证

**风险**:
- `.+`+review 规则可能产生较多软 sink(尤其 `execute`/`query`),增加 `chain_verdict` 复核量。**缓解**:只对独特/可消歧 callee 用 `.+`;太通用的不加(§4.4);复核否决非真漏洞链。
- php parser lstrip `$` 影响 php member_call receiver 语义。**缓解**:无规则依赖 receiver 带 `$`(§5.1 验证);跑现有 php 测试回归。

**验证**:
- 单测:§6 全绿(含 fastjson 复现闭环原始版 INJ-01)。
- 真机(后置,依赖探测器落地):关轨重扫 `sentinel_dashboard`,对比 `code_index.json` 的 `sink_call_sites` 改前(3 个空壳 `llm-sink-hunter`)→ 改后(含 `java-fastjson-parseobject`/`java-httpclient-execute` 等硬规则 sink,`callee_name`/`dangerous_slots` 非空);`injection_gitnexus_queue.json` 从不存在 → 非空。
