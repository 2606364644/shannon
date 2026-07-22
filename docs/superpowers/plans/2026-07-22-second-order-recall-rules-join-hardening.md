# 二阶存储召回强化(写规则补 token + join 实体类↔表名归一化)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 GitNexus 轨二阶召回的两个真根因 -- ① `java-orm-save` 写规则无 `tok` 捕获组致 `storage_token` 恒为 `"unresolvable"`(主流 Java ORM save 写点根本 join 不上);② join token 口径错配(写端变量名 `user` ↔ 读端表名 `users`,字面不等落空)。让主流 ORM save + 原生 SQL read 能 join 成功产 `2ND-GN-*`,纯确定性增强,不上独立二次漏洞 agent。

**Architecture:** join 时懒加载写点文件源码(`source_provider(StorageWritePoint) -> bytes`),扫 `@Table` 注解 / 类命名约定 / receiver 三级 fallback 解析实体类名→表名映射,join 前对写读 token 双向归一(口径从"变量名字面"改"表名归一")。保守漏召 > 误连(归一不确定保持原值)。补 Python/Go/PHP 写规则(目前全空)。`_looks_user_tainted` 精度提升(识别 config/常量)。改动限 `data/storage_rules.yml` + `storage_detector.py` + `second_order_join.py` + `second_order_builder.py` + 测试,不碰 `vuln-*.txt`、不改 join keying contract、不改编排调用点。

**Tech Stack:** Python 3.12 / pytest / pydantic。测试对齐 `tests/code_index/test_second_order_*.py` + `test_storage_detector.py` 既有 fixture 风格(FuncBlock.source_code + source_provider kwarg bytes)。

## Global Constraints

- **铁律(CLAUDE.md §1):确定性产物不喂 LLM 轨 prompt。** 本 plan 只动 GitNexus 轨确定性层,**禁碰 `vuln-*.txt`**。`packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py`(Task 7)须保持绿。
- **保守漏召 > 误连**:归一化不确定时 token 保持原值(漏召交 LLM 轨),**绝不误连**(假阳性二阶比漏报更糟)。
- **join keying contract 不变**(second_order_join.py:8-12):`reads_by_id` keyed by `param_name`、`CandidateChain.source_param == read's param_name`。归一化层加在 join **之前**,不改 keying 契约。`activities.py:1405-1409` 调用点签名新增 `source_provider` 参数(编排层**仅此一处**改动,Task 6)。
- **Gap A 持守**(commit `2bbd2947`):单跳 builder(inj/xss/ssrf)仍 suppress `source_type == STORAGE` 链,second_order_builder 仍是 STORAGE 链唯一权威。本 plan 不改单跳 suppress。
- **fail-fast 边界不变**:二阶仍归 inj/xss queue(DEGRADABLE),builder 异常仍被 `activities.py:1416-1418` 捕获降级(不终止),不新增 fail-fast。
- **测试陷阱(CLAUDE.md §3):全套 pytest 有预存挂起/失败。** 只跑本 plan 改动的测试文件,勿广跑全套。预存失败 `test_build_code_index_threads` / `test_gitnexus_call_graph`(MagicMock await)与本 plan 无关,忽略。
- **DEFAULT_RULES 锚点**:`test_storage_detector.py` 若有 rule_id 全集断言,新增写规则须同步更新(Task 7 确认)。
- **commit**:conventional-commit + 中文正文,只 `git add` 该 task 的 src + test 文件。分支 `feat/fork-py`。
- **非目标**:跨文件实体定义(实体类在别的文件)不追--归 LLM 轨动态 token 同档;不开独立二次漏洞 agent(见 spec §1.3);不改 `judge_chain_verdict`(段④已可靠)。

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `data/storage_rules.yml` | 存储读写硬规则 | `java-orm-save` 补 receiver 捕获;补 Python/Go/PHP 写规则 + 对偶读规则 |
| `storage_detector.py` | 读写点检测 | `detect_storage_writes` 提 receiver 入 `callee_receiver`(现 None);补语言规则加载 |
| `second_order_join.py` | write↔read join | 新增 `_resolve_write_token`(懒加载文件解析)+ `_resolve_read_table` + `_normalize_token`(实体类↔表名归一);`extract_second_order_candidates` 新增 `source_provider` 参数 |
| `second_order_builder.py` | 二阶 finding 产出 | 透传 `source_provider` 给 join;强化 `_looks_user_tainted`(config/常量识别) |
| `activities.py`(编排) | 调用点 | 传 `source_provider`(按 StorageWritePoint.file_path 读源码)给 builder(Task 6 唯一编排改动) |
| `tests/code_index/test_second_order_join.py` | join 归一化测试 | 加 token 归一化 + 表名解析 + 误连防护测试 |
| `tests/code_index/test_storage_detector.py` | 检测器测试 | 加 ORM save receiver 捕获 + 补语言写规则测试 |
| `tests/code_index/test_second_order_builder.py` | 端到端测试 | 加 `save(UserEntity)`+`FROM users` 端到端 + taint 精度测试 |

> 全路径前缀:src = `packages/core/src/supernova_core/code_index/`,test = `packages/core/tests/code_index/`,编排 = `packages/whitebox/src/supernova_whitebox/pipeline/`。下文路径省略前缀,执行时补全。

---

## Task 1: 写规则补 receiver 捕获(`java-orm-save` + 补语言)

**Files:**
- Modify: `data/storage_rules.yml`(`java-orm-save` 改 pattern 补 receiver 组;新增 Python/Go/PHP 写规则)
- Modify: `storage_detector.py`(`detect_storage_writes` 提 receiver 入 `callee_receiver`)
- Test: `tests/code_index/test_storage_detector.py`(加 ORM save receiver 捕获 + 补语言命中测试)

**Interfaces:**
- `StorageWriteRule` 无需改结构(receiver 从 match group 提,不入 rule 字段);`detect_storage_writes` 产出 `StorageWritePoint.callee_receiver` 从 `None` 改为提取得到的 receiver 字符串(如 `repo`)。
- `java-orm-save` pattern: `(?:save|persist|merge)\(` -> 补可选 receiver 前缀捕获。注意 ORM save 本身无表名 token(receiver 才是表名推断线索),故**不设 `tok` 组**,改为让 detector 提 receiver(Task 2 用 receiver 推断表名)。

**Steps:**
- [ ] **TDD-红**:`test_storage_detector.py` 加 `test_orm_save_captures_receiver` -- `repo.save(u)` -> `StorageWritePoint.callee_receiver == "repo"`(现 None,失败)。
- [ ] 改 `storage_rules.yml` `java-orm-save` pattern 为可捕获 receiver 的形式(如 `(?:(\w+)\.)?(?:save|persist|merge)\(`,receiver 在 group 1)。
- [ ] 改 `detect_storage_writes`:从 match 提 group(1) receiver 入 `callee_receiver`(无 receiver 则 None,保留裸 save 兼容)。
- [ ] 补语言写规则 + 测试:Python `session.add` / `db.add`(db)、`cache.set` / `redis.set`(cache);Go `db.Create`/`db.Save`/`db.Updates`(gorm,db);PHP `Model::create` / `$model->save` / `DB::table()->insert`(db)。每条规则加对应 `test_*` 命中测试。
- [ ] **TDD-绿**:跑 `uv run --package supernova-core pytest packages/core/tests/code_index/test_storage_detector.py -v` 全绿。
- [ ] **commit**:`feat(code_index): storage 写规则补 ORM receiver 捕获 + Python/Go/PHP 写规则`。

**Verification:**
- `StorageWritePoint.callee_receiver` 非 None(repo.save 场景)。
- Python/Go/PHP 写规则命中各自测试。
- 既有 4 条写规则不回归(原 test_storage_detector.py 4 passed)。

---

## Task 2: 写端表名解析(`_resolve_write_token` 懒加载文件)

**Files:**
- Modify: `second_order_join.py`(新增 `_resolve_write_token` + `source_provider` 参数)
- Test: `tests/code_index/test_second_order_join.py`(加表名解析测试)

**Interfaces:**
- `extract_second_order_candidates(writes, read_chains, *, reads_by_id, source_provider)` -- 新增 kw-only `source_provider: Callable[[StorageWritePoint], bytes | None]`。
- `_resolve_write_token(write: StorageWritePoint, source_text: str | None) -> str` -- 三级 fallback 返回表名;失败返回原 `write.storage_token`(可能 "unresolvable")。

**三级 fallback(确定性,仅同文件):**
1. **显式注解**:扫 source_text `@Table(name="<t>")` / `@TableName("<t>")` / `@Document("<t>")`,按实体类名(class 声明 `class <Entity>` 在注解附近)绑定。简单做法:扫文件内所有 `@Table(name="X")` 后跟 `class Y` -> map{Y: X}。
2. **命名约定**:无注解时,从 `write.callee_receiver`(`userRepository` / `userRepo`)去 `Repository`/`Repo` 后缀 -> `User` -> 驼峰转下划线复数 `users`。
3. **receiver 直接是表名**:`db.save` 无 receiver 语义 -> 保持原 storage_token(保守)。

**Steps:**
- [ ] **TDD-红**:`test_second_order_join.py` 加 `test_resolve_write_token_from_table_annotation` -- `@Table(name="users") class User` + `repo.save(u)` -> `_resolve_write_token` 返回 `users`(失败,函数不存在)。
- [ ] 实现 `_resolve_write_token`:接受 source_text,扫注解建 map,按 write 的 callee_receiver / 实体类名查表 + 命名约定 fallback。
- [ ] **TDD-红**:`test_resolve_write_token_by_naming_convention` -- 无注解,`userRepository.save(u)` -> `users`。
- [ ] 实现/补全命名约定 fallback(驼峰转下划线 + 复数)。
- [ ] **TDD-红**:`test_resolve_write_token_unresolvable_when_no_context` -- 裸 `save(u)` 无 receiver 无注解 -> 返回原 token(保守,不编造)。
- [ ] **TDD-绿**:跑 `uv run --package supernova-core pytest packages/core/tests/code_index/test_second_order_join.py -v` 全绿。
- [ ] **commit**:`feat(code_index): second_order_join 写端表名懒加载解析(@Table/命名约定/receiver)`。

**Verification:**
- `@Table(name="users")` + `User` 实体 -> 表名 `users`。
- `userRepository.save` 无注解 -> 命名约定 `users`。
- 无上下文 -> 原值不编造(保守)。

---

## Task 3: 读端表名解析(`_resolve_read_table`)

**Files:**
- Modify: `second_order_join.py`(强化 `_read_token` -> `_resolve_read_table`)
- Test: `tests/code_index/test_second_order_join.py`(加读端表名提取测试)

**Interfaces:**
- `_resolve_read_table(read_src: SourcePoint) -> str` -- 从 `expression` 提 `FROM <table>` / `INTO <table>` 表名;ORM 查询(`findOneBy*`)无表名则返回 receiver/实体类线索(供归一化)。

**Steps:**
- [ ] **TDD-红**:`test_resolve_read_table_from_sql_from` -- `expression="SELECT * FROM users WHERE id=?"` -> `users`。
- [ ] 实现 `_resolve_read_table`:正则提 `FROM\s+(\w+)` / `INTO\s+(\w+)`;ORM `findOneBy*` 返回 `param_name`(属性名,待归一化对齐)。
- [ ] **TDD-红**:`test_resolve_read_table_orm_returns_param` -- `findOneByName` -> 返回 `Name`(属性名,无表名,交归一化)。
- [ ] **TDD-绿**:全绿。
- [ ] **commit**:`feat(code_index): second_order_join 读端表名解析(FROM/INTO 提取)`。

**Verification:**
- 原生 SQL 读提表名 `users`。
- ORM 读返回属性名(交归一化,不编造表名)。

---

## Task 4: token 归一化层(`_normalize_token`)

**Files:**
- Modify: `second_order_join.py`(新增 `_normalize_token` + 接入 `extract_second_order_candidates`)
- Test: `tests/code_index/test_second_order_join.py`(加归一化 + 误连防护测试)

**Interfaces:**
- `_normalize_token(token: str, entity_table_map: dict[str, str]) -> str` -- 实体类名/receiver 归一到表名;不确定返回原 token。

**归一规则:**
- `UserEntity` / `User` -> 查 map 或命名约定 -> `users`
- `userRepository` / `userRepo` -> 去后缀 -> `User` -> `users`
- 已是表名(`users` 含下划线或全小写复数)-> 原值
- map 命中优先于命名约定

**Steps:**
- [ ] **TDD-红**:`test_normalize_token_aligns_entity_class` -- `UserEntity` + map{`UserEntity`:`users`} -> `users`。
- [ ] 实现 `_normalize_token`:map 查表 + receiver 去后缀 + 命名约定 fallback;未命中返原值。
- [ ] **TDD-红**:`test_normalize_token_keeps_original_when_unsure` -- 无 map 无约定线索的 token -> 原值(误连防护)。
- [ ] **TDD-红**:`test_no_false_join_unrelated_tokens` -- write `UserEntity`(->users) + read `orders`(无归一线索) -> **不配对**(误连防护核心)。
- [ ] 接入 `extract_second_order_candidates`:写读 token 都过 `_normalize_token` 后再比对;`source_provider` 传给 `_resolve_write_token` 建 map(每文件缓存一次)。
- [ ] **TDD-绿**:全绿。
- [ ] **commit**:`feat(code_index): second_order_join token 归一化层(实体类↔表名,保守不误连)`。

**Verification:**
- `UserEntity`/`User`/`userRepository`/`users` 四种写法归一到 `users`。
- 无线索 token 保持原值,不误连。
- 动态 token(`+`/`${`)仍 skip(回归 `test_dynamic_token_unresolvable_not_joined`)。

---

## Task 5: write taint 精度提升(`_looks_user_tainted`)

**Files:**
- Modify: `second_order_builder.py`(强化 `_looks_user_tainted`)
- Test: `tests/code_index/test_second_order_builder.py`(加 taint 精度测试)

**现状**(`second_order_builder.py:25-41`):纯数字/单引号字符串 -> False,其余 -> True(过粗,`config.timeout` 误判 tainted)。

**强化:** 识别非用户可控模式:
- 全大写命名(`DEFAULT_ROLE` / `MAX_SIZE`)-> 常量,not tainted
- config/i18n/env 前缀(`config.` / `i18n.` / `env.` / `settings.`)-> 配置,not tainted
- 枚举(`Color.RED`)-> not tainted
- 其余保持 tainted(保守方向,只减误报不增漏报)

**Steps:**
- [ ] **TDD-红**:`test_looks_user_tainted_config_not_tainted` -- `config.timeout` -> False(现 True,失败)。
- [ ] 强化 `_looks_user_tainted`:加全大写/config 前缀/枚举识别 -> False。
- [ ] **TDD-红**:`test_looks_user_tainted_constant_not_tainted` -- `DEFAULT_ROLE` -> False。
- [ ] **TDD-绿**:`uv run --package supernova-core pytest packages/core/tests/code_index/test_second_order_builder.py -v` 全绿。
- [ ] **commit**:`feat(code_index): second_order write taint 精度(识别 config/常量/枚举)`。

**Verification:**
- `config.timeout` / `DEFAULT_ROLE` / `Color.RED` -> not tainted。
- `user.name` / `req.body.x` -> tainted(不退化)。
- 既有 `test_second_order_xss_when_write_tainted_and_read_vuln` 不回归(`user.bio` 仍 tainted)。

---

## Task 6: builder 透传 source_provider + 编排接入

**Files:**
- Modify: `second_order_builder.py`(`build_second_order_findings` 签名加 `source_provider`,透传给 join)
- Modify: `activities.py`(调用点传 source_provider)
- Test: `tests/code_index/test_second_order_builder.py`(加端到端)+ `packages/whitebox/tests/pipeline/test_run_gitnexus_chain_verdict_second_order.py`(编排集成)

**Interfaces:**
- `build_second_order_findings(writes, pgraph, *, llm_client, sink_call_sites, reads_by_id, source_provider, progress_cb=None)` -- 新增 `source_provider`。
- 编排层:`source_provider = lambda w: _read_file_bytes(repo / w.file_path)`(按 StorageWritePoint.file_path 读源码;文件缺失返 None,join 降级保守)。

**Steps:**
- [ ] `build_second_order_findings` 签名加 `source_provider` kw-only 参数,透传给 `extract_second_order_candidates`。
- [ ] `activities.py:1405-1409` 调用点构造 source_provider(按 repo root + write.file_path 读),传入 builder。
- [ ] **TDD**:端到端测试 `test_save_entity_joins_from_sql_read` -- `repo.save(UserEntity)`(@Table users)+ `SELECT ... FROM users` read -> 产 `2ND-GN-*`(write tainted + read vulnerable)。
- [ ] **TDD**:编排集成测试更新(若有 source_provider 断言)。
- [ ] **TDD-绿**:`uv run --package supernova-core pytest packages/core/tests/code_index/test_second_order_builder.py -v` + `uv run --package supernova-whitebox pytest packages/whitebox/tests/pipeline/test_run_gitnexus_chain_verdict_second_order.py -v` 全绿。
- [ ] **commit**:`feat(whitebox): second_order builder 接 source_provider 懒加载解析(端到端 join 成功)`。

**Verification:**
- `save(UserEntity)` write + `FROM users` read 端到端产 `2ND-GN`。
- 编排层 source_provider 文件缺失时不崩(返 None,join 降级保守)。

---

## Task 7: 回归 + 铁律守恒 + 全量验证

**Files:**
- All test files touched + 守铁律锚点测试

**Steps:**
- [ ] 跑全部二阶相关测试:`test_second_order_join.py` + `test_second_order_builder.py` + `test_storage_detector.py` + `test_storage_models.py` + `test_storage_discovery_llm.py` + `test_storage_chain_propagator.py` + `test_storage_orchestration.py` + `test_run_gitnexus_chain_verdict_second_order.py` 全绿(子项⑤ 26 个不回归 + 本 plan 新增)。
- [ ] 守铁律:`uv run --package supernova-core pytest packages/core/tests/prompts/test_static_dataflow_hints_decoupling.py -v` 绿(未喂 LLM 轨)。
- [ ] gap A 回归:`test_single_hop_xss_builder_suppresses_storage_sourced_chain` 绿(单跳仍 suppress STORAGE)。
- [ ] sink 硬规则不回归:`test_sink_detector.py` + `test_sink_discovery_llm.py`(上轮修复 29+65 绿)。
- [ ] 预存失败确认无关:`test_build_code_index_threads` / `test_gitnexus_call_graph` 失败与本 plan 无关(忽略)。
- [ ] **commit**(若需修锚点):`test(code_index): second_order 召回强化回归 + 铁律锚点`。

**Verification:**
- 全部二阶测试绿,既有 26 个不回归。
- 铁律锚点绿。
- gap A / sink 硬规则不回归。

---

## Task 8: 真机冒烟(后置,用户执行)

**非代码 task**:`sentinel_dashboard` 关轨重扫,验 `2ND-GN-*` 非空。

**Steps:**
- [ ] `SUPERNOVA_LLM_TRACK_ENABLED=0 uv run --package supernova-whitebox shannon-whitebox start --repo .../sentinel_dashboard`。
- [ ] 验 `deliverables/xss_gitnexus_queue.json` / `injection_gitnexus_queue.json` 含 `2ND-GN-*` finding 非空。
- [ ] 验 `code_index.json` 的 `storage_write_points` 含 `callee_receiver` 非 None(ORM save)。
- [ ] 对照子项⑤ 待冒烟项(memory `second-order-storage-taint-dual-track-spec`)一并验证。
- [ ] 记录结果到 memory(本 plan + 子项⑤)。

**Verification:**
- `2ND-GN-*` 真机非空(子项⑤ + 本 plan 共同目标)。
- 无回归崩溃。

---

## Risk Mitigation

| 风险 | 缓解 |
|---|---|
| 表名解析误判(`User`->`user` 而非 `users`)| 命名约定 fallback 只在能确认时用;不确定存原值(保守漏召 > 误连,Global Constraints) |
| 跨文件实体定义漏解析 | 范围限定同文件;跨文件归 LLM 轨动态 token 同档(非目标) |
| 归一化引入误连(假阳性二阶)| Task 4 `test_no_false_join_unrelated_tokens` 锁定;双向归一不确定保持原值 |
| 候选数膨胀(笛卡尔积)| Task 5 taint 精度前置过滤,减少送 LLM 判定量 |
| source_provider 文件 IO | 每文件缓存(Task 4 接入时);文件缺失返 None 降级保守(Task 6) |
| 关轨 fail-fast 边界 | 二阶 builder 异常仍被 activity 捕获降级,不新增 fail-fast(Global Constraints) |

---

## Related

- spec:`docs/superpowers/specs/2026-07-22-second-order-recall-rules-join-hardening-design.md`
- 前置:`docs/superpowers/specs/2026-07-21-second-order-storage-taint-dual-track-design.md`(子项⑤,已落地)
- 同期模式:`docs/superpowers/plans/2026-07-21-sink-rules-hardening.md`(规则外部化 + TDD 风格,本 plan 对齐)
- memory:`second-order-recall-rules-join-hardening`、`second-order-storage-taint-dual-track-spec`、`sink-rules-hardening-status`
