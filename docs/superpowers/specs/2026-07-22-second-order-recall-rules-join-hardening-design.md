# 二阶存储召回强化:写规则补 token + join 上下文解析(GitNexus 轨)

> 状态:design(待 plan)。分支 `feat/fork-py`。
> 起源:2026-07-22 会话,"`second_order_builder.py` 够用吗"评估 -> 结论:瓶颈在召回(写点识别窄 + token join 脆弱),不在判定深度,不上独立二次漏洞 agent,走"补规则 + 强化 join"性价比路径。
> 关系: [`2026-07-21-second-order-storage-taint-dual-track-design`](./2026-07-21-second-order-storage-taint-dual-track-design.md) 的 **GitNexus 轨增强**(不改 LLM 轨,守铁律)。前置工作已落地(子项⑤ 8 task + gap A),本 spec 只强化召回广度与 join 鲁棒性。

---

## 1. 背景与根因

### 1.1 现状(子项⑤ 已落地后的能力边界)

GitNexus 轨二阶召回链路四段,可靠性呈**倒金字塔**--read 端判定可靠,往前每步漏召放大:

| 段 | 实现 | 可靠性 | 问题 |
|---|---|---|---|
| ① 写点识别 | `storage_detector` + `storage_rules.yml`(4 写规则) | **最弱** | 规则窄 + 主流写点 token 提不出 |
| ② write↔read join | `second_order_join`(字面 token 精确匹配) | **弱** | 写端表名 ↔ 读端变量名对不上;动态 token skip |
| ③ write taint 检查 | `_looks_user_tainted`(纯启发式) | 中(保守误报) | 只误报不漏报,靠 read 端纠偏 |
| ④ read 端判定 | `judge_chain_verdict`(轻量 LLM) | **强** | 复用单跳,完整 |

### 1.2 两个真根因(实证)

**根因 A:`java-orm-save` 写规则无 `tok` 捕获组,`storage_token` 永远是 `"unresolvable"`。**

`storage_rules.yml:48-52`:
```yaml
- rule_id: java-orm-save
  pattern: "(?:save|persist|merge)\\("   # 无 ?P<tok> 捕获组
```
`storage_detector.py:190-196` 逻辑:无 `tok` 组 -> `token = "unresolvable"`。而 `second_order_join.is_resolvable_token` 直接排除 `"unresolvable"`(`second_order_join.py:31`)。**后果:最主流的 Java ORM save 写点,确定性路径根本 join 不上**,只能靠 `storage_discovery_llm`(Task4 hunter)兜,而后者依赖 LLM 可用。

同理 `java-orm-find` 读规则(`storage_rules.yml:25-29`)用 `param_of: tok` 捕获的是属性名(`findOneByName` 的 `Name`),不是表名,与写端无法对齐。

**根因 B:join 的 token 口径错配--写端表名(实体类/注解)↔ 读端字面量(`"users"` 表名字符串)。**

`second_order_join._read_token`(`second_order_join.py:41-46`)优先从 `read_src.expression` 提字面量(`"users"` / `'user_prefs'`),回退 `param_name`(变量名)。但写端 `save(user)` 提的是实体**变量名 `user`**,不是表名 `users`。两者字面不等,join 落空。动态 token(`+`/`${`/函数调用)`is_resolvable_token` 直接 skip 并标注"交给 LLM 轨"(`second_order_join.py:3-6`)。

### 1.3 为什么不上"二次漏洞 agent"

评估结论(2026-07-22):**不开独立二次漏洞 agent**。理由:

1. **瓶颈在召回(①②),不在判定深度**。④ read 端已用 LLM;agent 补判定深度对召回无能为力--漏召才是主要问题(待真机冒烟验证的就是"2ND-GN 非空")。
2. **agent 也得先有候选才能判**。若 ① 没识别写点、Task4 hunter 也没兜到,agent 连输入都没有。**开 agent 不替代补规则**。
3. **代价**:二阶候选 = |writes|×|reads| 笛卡尔积,大仓上千对。`judge_chain_verdict` 单次结构化(便宜)换 agent 多轮,成本量级跃升;且二阶归在 inj/xss queue(DEGRADABLE),agent 化后 fail-fast 边界更脆。
4. **LLM 轨重叠**:现有 `vuln-xss.txt` 已有 Stored XSS 方法论(`:165` Database Read Checkpoint),另开 LLM 轨二次漏洞 agent 与之重复;改 vuln-xss 让它追 write 端则破坏其自给自足铁律。**性价比低。**

> 本 spec 走"补规则 + 强化 join"路径,把召回广度做扎实。若真机冒烟后发现动态 token 漏召严重,再单独评估轻量 LLM join 步骤(非 agent,守 GitNexus 轨轻量判定定位),见 §6 follow-up。

---

## 2. 目标

提升 GitNexus 轨二阶**召回广度**与 **join 鲁棒性**,纯确定性增强:

1. **写点 token 提取(根因 A)**:让主流 ORM save 不再永远是 `"unresolvable"`,能提出可 join 的 token。
2. **join token 对齐(根因 B)**:写端表名 ↔ 读端表名归一化对齐,不只靠变量名字面匹配。
3. **write taint 精度(段 ③)**:减少 `_looks_user_tainted` 对配置/常量的误判(保守方向的误报,降低下游 LLM 成本)。
4. **补语言覆盖**:Python / Go / PHP 的 ORM 写规则(目前全空)。

**非目标**:
- **不动 LLM 轨**(`vuln-*.txt`),守铁律「确定性产物不喂 LLM 轨 prompt」。
- **不开独立二次漏洞 agent**(见 §1.3)。
- **不改 `judge_chain_verdict`**(段 ④ 已可靠)。
- **不动跨服务二阶**(归开轨,旧 spec §2 已界定)。

---

## 3. 架构

### 3.1 总览(四段改动落点)

```
① 写点识别 storage_detector + storage_rules.yml
   ├─ 写规则补 tok 捕获(能提字面 token 的:cache.set/writeFile/setProperty 已有)
   ├─ ORM save:规则补 receiver 捕获(repo.save 的 repo)-> builder 层上下文解析表名
   └─ 补 Python/Go/PHP 写规则
② join 强化 second_order_join(核心改动)
   ├─ 写端表名解析:从实体类/@Table 注解/变量名推断 token(新 _resolve_write_token)
   ├─ 读端表名解析:从 FROM/表名 SQL 提取(新 _resolve_read_table)
   ├─ token 归一化:实体类名 ↔ 表名映射(UserEntity <-> users),写读对齐
   └─ 笛卡尔积不变,token 对齐口径改为"表名"而非"变量名"
③ write taint 精度 _looks_user_tainted(second_order_builder)
   └─ 识别 config/常量/枚举(非用户可控),减少误报
④ read 端判定 judge_chain_verdict —— 不动
```

### 3.2 写端表名解析策略(根因 B 核心)

Java ORM `save(entity)` 无法从调用点静态拿表名(表名在 `@Table(name="users")` 注解或实体类命名约定)。**确定性上下文解析**(用户已确认方向),分三级 fallback:

1. **显式注解**:扫同文件 `@Table(name="<table>")` / `@TableName("<table>")`(MyBatis-Plus)/ `@Document("<collection>")`(Spring Data MongoDB),绑定到对应实体类。
2. **命名约定**:实体类名 `UserEntity` / `User` -> 表名 `users`(驼峰转下划线 + 复数,主流 Spring/JPA 约定)。
3. **receiver 类型推断**:`userRepository.save(u)` 的 receiver `userRepository` -> 去除 `Repository` 后缀 -> `User` -> 表名 `users`。

解析产物:**实体类名 -> 表名** 的 file 级映射(一个文件内扫一次),供 join 归一化。写点 `storage_token` 存**表名**(若能解析),无法解析存 `"unresolvable"`(现状不变,交 Task4 hunter / LLM 轨)。

> 范围限定:只解析**同文件内**可见的实体类/注解/命名约定。跨文件实体定义(实体类在别的文件)不追--那需跨文件 AST,成本超本 spec 范围,留给动态 token 同档(交 LLM 轨)。即本 spec 覆盖"实体定义与 save 同文件"的主流情况(单体仓常见)。

### 3.3 读端表名解析(根因 B 对偶)

`_read_token` 现状:优先 `expression` 字面量,回退 `param_name`。问题:ORM 查询 `findOneByName(name)` 的 `param_name` 是 `Name`(属性名),非表名;原生 SQL `SELECT ... FROM users` 的表名在 `expression` 里但需提取。

强化:
1. 原生 SQL 读:从 `expression` 提 `FROM <table>` / `INTO <table>` 的表名(新正则)。
2. ORM 读(`findOneBy*` / `findById`):无表名信息,`storage_token` 存**实体类名或 receiver**(如 `userRepository.findOne` -> `userRepository`),供 join 经归一化映射对齐到写端表名。
3. **归一化对齐**:join 前把写读 token 都过 `_normalize_token`(实体类名 <-> 表名),口径统一到表名。

### 3.4 token 归一化层(新 `_normalize_token`)

`second_order_join` 新增归一化函数,join 前对写读 token 双向归一:

- `UserEntity` / `User` / `userRepository` / `users` -> 归一到 `users`(表名)
- 映射来源:§3.2 的 file 级"实体类名 -> 表名"映射 + 命名约定 fallback
- 无法归一的 token 保持原值(保守,可能 join 不上 -> 漏召交 LLM 轨,不误连)

**铁律守恒**:归一化是**纯确定性同文件启发式**,不引 LLM、不喂 LLM 轨 prompt。只改 GitNexus 轨内部 token 对齐口径。

### 3.5 补语言写规则(段 ① 广度)

| 语言 | 写规则新增 | medium |
|---|---|---|
| Python | `session.add` / `db.session.commit` 前的 `add` / SQLAlchemy `session.merge` | db |
| Python | `cache.set` / `redis.set` | cache |
| Go | `db.Create` / `db.Save` / `db.Updates`(gorm) | db |
| PHP | `Model::create` / `$model->save` / `DB::table()->insert` | db |

写规则 pattern 补 `tok` 捕获组(能提字面 token 的);ORM 类(`db.Create` / `session.add`)走 §3.2 上下文解析。读规则同步补对应语言(对偶)。

---

## 4. 不变量与铁律

1. **守铁律**(CLAUDE.md §1):本 spec 全程**不碰 LLM 轨 `vuln-*.txt`**,不 `@include` 确定性产物。`test_static_dataflow_hints_decoupling.py` 锁定的不变量不受影响。
2. **join 不变 contract**:`reads_by_id` keyed by `param_name`、`CandidateChain.source_param == read's param_name`(second_order_join.py:8-12 文档约束)。归一化层加在 join **之前**,不改 keying 契约,不改 Task 7/8 调用方签名。
3. **保守漏召 > 误连**:归一化不确定时保持原值(漏召交 LLM 轨),**绝不误连**(把不相关的 write↔read 配对会产生假阳性二阶漏洞)。
4. **`externally_exploitable` 不被覆写**(CLAUDE.md §1):`2ND-GN` finding 的 reachability 标签仍由 activity 层 per-route 精炼,builder 只置 placeholder。
5. **fail-fast 边界不变**:二阶仍归 inj/xss queue(DEGRADABLE),builder 异常被 `activities.py:1416-1418` 捕获降级(不终止),不引入新的 fail-fast 路径。
6. **Gap A 持守**(commit `2bbd2947`):单跳 builder(inj/xss/ssrf)仍 suppress `source_type == STORAGE` 链,second_order_builder 仍是 STORAGE 链唯一权威。本 spec 只强化 second_order_builder,不改单跳 suppress。

---

## 5. 测试策略(TDD)

对齐既有 `test_second_order_*.py` 风格(`packages/core/tests/code_index/`)。每个 task 先写失败测试再实现。

| 测试文件(新增/扩展) | 覆盖 |
|---|---|
| `test_second_order_join.py` 扩展 | §3.4 归一化:`UserEntity`↔`users`↔`userRepository` 对齐;无法归一保持原值不误连 |
| `test_second_order_join.py` 扩展 | §3.2/3.3 写端表名解析(`@Table` 注解/命名约定/receiver)+ 读端 `FROM <table>` 提取 |
| `test_storage_detector.py` 扩展 | §3.5 Python/Go/PHP 写规则命中;ORM save 带 receiver 捕获 |
| `test_second_order_builder.py` 扩展 | §3.6 端到端:`save(UserEntity)` write + `FROM users` read -> join 成功产 `2ND-GN` |
| `test_second_order_builder.py` 扩展 | §3 write taint 精度:`config.timeout` / `DEFAULT_ROLE` / `i18n.msg` -> not tainted |
| `test_second_order_join.py` 扩展 | 回归:动态 token(`+`/`${`)仍 skip 交 LLM 轨(不退化) |

**预存失败注意**(feat-fork-py-test-gotchas):只跑改动相关测试文件,不广跑全套;`test_build_code_index_threads` / `test_gitnexus_call_graph` 预存失败与本 spec 无关。

**真机冒烟(后置)**:`sentinel_dashboard` 关轨重扫(`SUPERNOVA_LLM_TRACK_ENABLED=0`),验 `xss_gitnexus_queue.json` / `injection_gitnexus_queue.json` 含 `2ND-GN-*` 非空(子项⑤ spec 待冒烟项,本 spec 完成后一并验)。

---

## 6. 实现路径(粗粒度,待 plan 细化)

1. **写规则补 tok + receiver**(`storage_rules.yml` + `storage_detector.py`):ORM save 规则补 receiver 捕获;补 Python/Go/PHP 写规则;`detect_storage_writes` 提 receiver 入 `callee_receiver`(现为 None)。
2. **写端表名解析**(新 `_resolve_write_token` in `second_order_join` 或新 module):file 级 `@Table`/命名约定/receiver 三级 fallback,产"实体类名 -> 表名"映射。
3. **读端表名解析**(强化 `_read_token`):原生 SQL `FROM`/`INTO` 提取;ORM 查询存实体类名/receiver。
4. **token 归一化层**(新 `_normalize_token`):join 前写读 token 归一到表名;不确定保持原值。
5. **join 接入归一化**(`extract_second_order_candidates`):token 对齐口径从"变量名字面"改"表名归一";笛卡尔积不变。
6. **write taint 精度**(强化 `_looks_user_tainted`):识别 config/常量/枚举/全大写命名(非用户可控)。
7. **TDD 测试**:每步先写失败测试,全绿后真机冒烟。

**预计文件改动**:5-7 个(`storage_rules.yml`、`storage_detector.py`、`second_order_join.py`、`second_order_builder.py` + 对应 4 个测试文件)。无编排层改动(join contract 不变,`activities.py` 调用点零改)。

---

## 7. 风险与权衡

| 风险 | 缓解 |
|---|---|
| 表名解析误判(`User` -> `user` 而非 `users`)| 命名约定 fallback 只在能确认时用;不确定存原值(漏召 > 误连,§4 铁律 3) |
| 跨文件实体定义漏解析 | 范围限定同文件(§3.2);跨文件归 LLM 轨动态 token 同档 |
| 归一化引入误连(假阳性二阶)| 双向归一不确定时保持原值,不强行对齐(§3.4) |
| 写规则补全后候选数膨胀(笛卡尔积)| `_looks_user_tainted` 精度提升(段③)前置过滤,减少送 LLM 判定的候选量 |
| 关轨 fail-fast 边界 | 二阶 builder 异常仍被 activity 捕获降级,不新增 fail-fast(§4 铁律 5) |

---

## 8. 验收标准

1. `java-orm-save` 写点不再恒为 `"unresolvable"`(能提表名或实体类名)。
2. `save(UserEntity)` write + `SELECT ... FROM users` read 能 join 成功产 `2ND-GN`(端到端测试)。
3. 归一化层对 `UserEntity`/`User`/`userRepository`/`users` 四种写法对齐。
4. 动态 token(`+`/`${`)仍 skip(不退化,交 LLM 轨)。
5. `_looks_user_tainted` 对 `config.timeout`/`DEFAULT_ROLE` 判 not tainted。
6. Python/Go/PHP 写规则命中(对应测试)。
7. 既有二阶测试(26 个)不回归 + 子项⑤ gap A suppress 不破。
8. 真机冒烟:`sentinel_dashboard` 关轨重扫 `2ND-GN-*` 非空。

---

## 9. 关联

- 前置:[`2026-07-21-second-order-storage-taint-dual-track-design`](./2026-07-21-second-order-storage-taint-dual-track-design.md)(子项⑤,已落地)
- 同期:[`2026-07-21-sink-rules-hardening-design`](./2026-07-21-sink-rules-hardening-design.md)(sink 规则 receiver_pattern 修复,本 spec 复用其"规则外部化 + rp 失配修复"模式)
- 架构不变量:CLAUDE.md §1 双轨铁律、`code-index-deterministic-asset-layer` spec
- memory:`second-order-storage-taint-dual-track-spec`(待真机冒烟,本 spec 完成后一并验)
