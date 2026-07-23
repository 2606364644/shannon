# PoC 分层确定性化 + 可靠性加固 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 PoC 生成从「逐条 LLM、20min 必超时、拖垮 workflow」变为「分层模板优先 + 按 controller 文件分组 LLM 只补缺口 + 失败绝不阻塞主流程」。

**Architecture:** 报告增强层重构。inj/xss/ssrf 改为分层组装：先确定性提取参数名/controller 文件/方法 → 模板命中则 0ms → 缺 route/witness 则按 controller 文件分组、每组一次轻量 LLM 只补 {method,route,witness} → 模板组装。authz（成对模板）/ auth（量小）保持既有 per-item 路径。外加：workflow 层 try/except 硬保 §8 非阻塞契约（Fix A）、`.poc_checkpoint.json` 断点续传（Fix B）、str items bug 止血。

**Tech Stack:** Python 3.12 / asyncio / Temporalio（workflow + activity）/ pytest / 现有 `run_claude_prompt` 抽象。

## Global Constraints

- **不动双轨 / verdict / merger / chain_verdict prompt**（CLAUDE.md §1 铁律）。所有改动限：`packages/core/src/supernova_core/services/poc_generator.py`、两轨 `workflows.py` 的 PoC activity 调用包裹、对应测试。
- **新分层流程仅适用于 inj/xss/ssrf**；authz 的 `_build_authz_pair`、auth 的 per-item `llm_fill_gap` 保持既有路径不动。
- **产物格式不变**：curl + Burp 双格式、置信度三档、md 布局、文件名 `exploitable_poc_collection.md`。
- **TDD**：每个任务先写失败测试 → 跑红 → 实现 → 跑绿 → commit。只跑改动相关测试文件（CLAUDE.md §3 测试陷阱，勿广跑全套）。
- **测试 mock 模式**（既有约定）：`import supernova_core.services.poc_generator as mod; monkeypatch.setattr(mod, "run_claude_prompt", fake_async)`，fake 返回 `SimpleNamespace(success=..., structured_output=..., error=...)`。
- PoC activity 配置：`start_to_close_timeout=20min` + `retry_for("poc")`（`POC_RETRY max 3`）维持不变。

---

## File Structure

- **Modify** `packages/core/src/supernova_core/services/poc_generator.py` —— 主战场：新增 `_coerce_str_dict`、`extract_gn_location`、`PartialSpec`、`_extract_deterministic`、`_assemble`、`_group_by_controller_file`、`llm_fill_gaps`/`GAPFILL_OUTPUT_SCHEMA`/`_batch_fill_gaps`；重构 `PoCGenerator.generate()` 主循环；改 `_spec_from_llm_guess`。
- **Modify** `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py:602-606` —— Fix A 包裹。
- **Modify** `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py:411-415` —— Fix A 包裹。
- **Test** `packages/core/tests/test_poc_generator.py` —— 扩充（新函数 + 集成）。
- **Test** `packages/whitebox/tests/test_retry_policy_coverage.py` —— 加 PoC 非阻塞 AST 锚点。
- **Test** `packages/blackbox/tests/test_retry_policy_coverage.py` —— 加 PoC 非阻塞 AST 锚点。

---

### Task 1: str items bug 止血（`_coerce_str_dict`）

LLM（GLM 无 strict）返回 str 类型 query/headers 时 `.items()` 崩（sentinel_dashboard INJ-GN-08 实测）。仿既有 `_coerce_request_body` 加 `_coerce_str_dict`，应用到 `_spec_from_llm_guess`。

**Files:**
- Modify: `packages/core/src/supernova_core/services/poc_generator.py`（`_spec_from_llm_guess` 及其上方新增 `_coerce_str_dict`）
- Test: `packages/core/tests/test_poc_generator.py`

**Interfaces:**
- Produces: `_coerce_str_dict(raw: Any) -> dict[str, str]`（模块级函数）

- [ ] **Step 1: 写失败测试**

追加到 `packages/core/tests/test_poc_generator.py` 末尾：

```python
def test_coerce_str_dict_handles_str_query_headers():
    """regression（sentinel_dashboard 2026-07-22 INJ-GN-08）:LLM 返回 str 类型
    query/headers（GLM 无 strict）曾致 'str' object has no attribute 'items'。"""
    from supernova_core.services.poc_generator import _coerce_str_dict
    # dict 直通
    assert _coerce_str_dict({"a": "1"}) == {"a": "1"}
    # None / 空
    assert _coerce_str_dict(None) == {}
    assert _coerce_str_dict("") == {}
    # query string
    assert _coerce_str_dict("a=1&b=2") == {"a": "1", "b": "2"}
    # JSON 对象字符串
    assert _coerce_str_dict('{"a": "1"}') == {"a": "1"}
    # 乱串不崩
    assert _coerce_str_dict("garbage") == {}


def test_spec_from_llm_guess_str_query_does_not_crash():
    """_spec_from_llm_guess 收到 str query/headers 不再崩。"""
    from supernova_core.services.poc_generator import _spec_from_llm_guess
    class V:
        ID = "X-1"
    spec = _spec_from_llm_guess(
        {"method": "GET", "path": "/x", "query": "a=1&b=2",
         "headers": "X-Test: y", "body": None}, V(), "injection", ConfidenceBand.SUSPECTED)
    assert spec.query == {"a": "1", "b": "2"}
    assert spec.headers == {}  # "X-Test: y" 非 k=v& 形态 → 空 dict（不崩即可）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py::test_coerce_str_dict_handles_str_query_headers packages/core/tests/test_poc_generator.py::test_spec_from_llm_guess_str_query_does_not_crash -x -q`
Expected: FAIL（`ImportError: cannot import name '_coerce_str_dict'`）。

- [ ] **Step 3: 实现 `_coerce_str_dict` 并应用到 `_spec_from_llm_guess`**

在 `poc_generator.py` 中 `_coerce_request_body` 函数**之后**新增：

```python
def _coerce_str_dict(raw: Any) -> dict[str, str]:
    """LLM structured_output 不可靠（GLM 无 strict），query/headers 可能返回 str 而非
    schema 声明的 object。归一化为 dict[str,str]，守 spec 类型不变量，避免
    'str' object has no attribute 'items'（2026-07-22 sentinel_dashboard INJ-GN-08 实测）。
    """
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        if s[:1] in ("{", "["):
            try:
                p = json.loads(s)
                return {str(k): str(v) for k, v in p.items()} if isinstance(p, dict) else {}
            except Exception:
                return {}
        out: dict[str, str] = {}
        for pair in s.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    return {}
```

修改 `_spec_from_llm_guess`（把 query/headers 两行改为用 `_coerce_str_dict`）：

```python
def _spec_from_llm_guess(guess: dict, vuln: Any, vuln_class: str, band: ConfidenceBand) -> HttpRequestSpec:
    return HttpRequestSpec(
        method=(guess.get("method") or "GET").upper(),
        path=guess.get("path") or "/",
        query=_coerce_str_dict(guess.get("query")),
        headers=_coerce_str_dict(guess.get("headers")),
        body=_coerce_request_body(guess.get("body")),
        auth_state=AuthState.UNKNOWN,
        confidence_band=band,
        source_id=getattr(vuln, "ID", ""),
        vuln_class=vuln_class,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py::test_coerce_str_dict_handles_str_query_headers packages/core/tests/test_poc_generator.py::test_spec_from_llm_guess_str_query_does_not_crash -x -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: 回归既有 poc 测试不破**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -x -q`
Expected: PASS（全部，含既有 `test_generate_llm_dict_body_does_not_crash`）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/services/poc_generator.py packages/core/tests/test_poc_generator.py
git commit -m "fix(core): PoC _spec_from_llm_guess 归一化 str query/headers 防 items 崩溃

LLM(GLM 无 strict)返回 str 类型 query/headers 曾致 'str' object has no
attribute 'items'(sentinel_dashboard INJ-GN-08)。仿 _coerce_request_body 加
_coerce_str_dict(dict/query-string/JSON/乱串均归一化为 dict)。"
```

---

### Task 2: `extract_gn_location` —— GitNexus source 确定性提取

从 GitNexus 轨 `source`（格式 `param (file:method:line)`）确定性提取参数名/controller 文件/方法，供分组与组装。

**Files:**
- Modify: `packages/core/src/supernova_core/services/poc_generator.py`（Task 5 区域 `build_llm_prompt` 之前新增）
- Test: `packages/core/tests/test_poc_generator.py`

**Interfaces:**
- Produces: `extract_gn_location(source: str | None) -> tuple[str | None, str | None, str | None]` —— `(param_name, file_path, method)`，非 GitNexus 格式返回 `(None, None, None)`

- [ ] **Step 1: 写失败测试**

追加到 `test_poc_generator.py`：

```python
def test_extract_gn_location_java_controller():
    from supernova_core.services.poc_generator import extract_gn_location
    src = "payload (src/main/java/com/alibaba/csp/sentinel/dashboard/controller/cluster/ClusterConfigController.java:apiModifyClusterConfig:70)"
    param, f, m = extract_gn_location(src)
    assert param == "payload"
    assert f == "src/main/java/com/alibaba/csp/sentinel/dashboard/controller/cluster/ClusterConfigController.java"
    assert m == "apiModifyClusterConfig"


def test_extract_gn_location_ts_handler():
    from supernova_core.services.poc_generator import extract_gn_location
    src = "userId (src/routes/user.ts:getUser:42)"
    param, f, m = extract_gn_location(src)
    assert param == "userId"
    assert f == "src/routes/user.ts"
    assert m == "getUser"


def test_extract_gn_location_non_gn_returns_none():
    from supernova_core.services.poc_generator import extract_gn_location
    # LLM 轨格式（无括号 file:method:line）→ None
    assert extract_gn_location("payload — @RequestBody at Foo.java:71") == (None, None, None)
    assert extract_gn_location(None) == (None, None, None)
    assert extract_gn_location("") == (None, None, None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k extract_gn_location -x -q`
Expected: FAIL（`ImportError: cannot import name 'extract_gn_location'`）。

- [ ] **Step 3: 实现 `extract_gn_location`**

在 `poc_generator.py` 的 `# Task 5: 富信息 LLM 补缺口` 注释段**之前**新增：

```python
_GN_SOURCE_RE = re.compile(
    r"^(\S+)\s*\((.+?):([^/:]+):(\d+)\)\s*$"
)


def extract_gn_location(source: str | None) -> tuple[str | None, str | None, str | None]:
    """从 GitNexus 轨 source 提取 (param_name, file_path, method)。

    GitNexus builder 的 _source_text 产 'param (file:method:line)' 形态
    （如 'payload (…/Controller.java:apiModifyClusterConfig:70)'）。
    file 可含 '/'/'.'；method 是单个标识符（不含 ':/'）；line 是纯数字。
    非 GitNexus 格式（LLM 轨的 '@RequestBody at Foo.java:71' 等）→ (None, None, None)。
    """
    if not source:
        return (None, None, None)
    m = _GN_SOURCE_RE.match(source.strip())
    if not m:
        return (None, None, None)
    return (m.group(1), m.group(2), m.group(3))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k extract_gn_location -x -q`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/services/poc_generator.py packages/core/tests/test_poc_generator.py
git commit -m "feat(core): PoC extract_gn_location 从 GitNexus source 确定性提取 param/file/method

为分层模板化(§4.2)奠基:GitNexus 轨 source 'param (file:method:line)' 可
确定性提取参数名/controller 文件/方法,供按文件分组与模板组装。"
```

---

### Task 3: `PartialSpec` + `_extract_deterministic` + `_assemble`

确定性提取部分 spec + 用 LLM gap-fill 字段组装最终 `HttpRequestSpec`。

**Files:**
- Modify: `packages/core/src/supernova_core/services/poc_generator.py`
- Test: `packages/core/tests/test_poc_generator.py`

**Interfaces:**
- Consumes: `extract_gn_location`（Task 2）、`derive_method_path`/`extract_param_name`/`find_endpoint_info`/`derive_auth_state`/`auth_header`/`_is_open_redirect`/`classify_confidence`（既有）
- Produces:
  - `PartialSpec`（dataclass）
  - `_extract_deterministic(vuln, vuln_class, endpoints, band) -> PartialSpec`
  - `_assemble(partial, gap, endpoints) -> HttpRequestSpec`（`gap: dict|None` = `{http_method, route_path, witness_payload}`）

- [ ] **Step 1: 写失败测试**

追加到 `test_poc_generator.py`：

```python
def test_extract_deterministic_gn_vuln_has_gaps():
    from supernova_core.services.poc_generator import _extract_deterministic
    class V:
        ID = "INJ-GN-01"
        source = "payload (src/main/java/x/Controller.java:apiModifyClusterConfig:70)"
        path = "payload -> src/main/java/x/Controller.java:apiModifyClusterConfig"
        endpoint = None
        source_endpoint = None
        witness_payload = None
        verdict = "vulnerable"
        confidence = "high"
    p = _extract_deterministic(V(), "injection", {}, ConfidenceBand.HIGH)
    assert p.param_name == "payload"
    assert p.controller_file == "src/main/java/x/Controller.java"
    assert p.method is None and p.path is None and p.witness is None
    assert p.needs_gap_fill is True


def test_extract_deterministic_llm_vuln_no_gap():
    from supernova_core.services.poc_generator import _extract_deterministic
    class V:
        ID = "INJ-VULN-01"
        source = "payload — @RequestBody String payload at C.java:71"
        path = "POST /cluster/config/modify_single -> apiModifyClusterConfig"
        endpoint = None
        source_endpoint = None
        witness_payload = '{"@type":"x"}'
        verdict = "vulnerable"
        confidence = "high"
    p = _extract_deterministic(V(), "injection", {}, ConfidenceBand.CONFIRMED)
    assert p.method == "POST" and p.path == "/cluster/config/modify_single"
    assert p.witness == '{"@type":"x"}'
    assert p.needs_gap_fill is False


def test_assemble_injection_with_gapfill():
    from supernova_core.services.poc_generator import _extract_deterministic, _assemble
    class V:
        ID = "INJ-GN-01"
        source = "payload (src/main/java/x/Controller.java:m:70)"
        path = "payload -> Controller.java:m"; endpoint = None; source_endpoint = None
        witness_payload = None; verdict = "vulnerable"; confidence = "high"
    p = _extract_deterministic(V(), "injection", {}, ConfidenceBand.HIGH)
    spec = _assemble(p, {"http_method": "POST", "route_path": "/cluster/config/modify_single",
                         "witness_payload": "1' OR '1'='1"}, {})
    assert spec.method == "POST"
    assert spec.path == "/cluster/config/modify_single"
    assert spec.query == {"payload": "1' OR '1'='1"}


def test_assemble_ssrf_defaults_post_body():
    from supernova_core.services.poc_generator import _extract_deterministic, _assemble
    class V:
        ID = "SSRF-GN-01"; source = "url (src/x/Fetch.java:fetchUrl:30)"
        path = "url -> Fetch.java"; endpoint = None; source_endpoint = None
        witness_payload = None; verdict = "vulnerable"; confidence = "high"
        suggested_exploit_technique = ""; vulnerable_parameter = None
    p = _extract_deterministic(V(), "ssrf", {}, ConfidenceBand.HIGH)
    spec = _assemble(p, {"http_method": "GET", "route_path": "/fetch",
                         "witness_payload": "http://evil/x"}, {})
    assert spec.method == "POST"  # ssrf 非 redirect 默认 POST
    assert spec.body == "url=http://evil/x"


def test_assemble_degrades_when_gap_empty():
    from supernova_core.services.poc_generator import _extract_deterministic, _assemble
    class V:
        ID = "INJ-GN-09"; source = "payload (src/x/C.java:m:70)"
        path = "payload -> C.java:m"; endpoint = None; source_endpoint = None
        witness_payload = None; verdict = "vulnerable"; confidence = "high"
    p = _extract_deterministic(V(), "injection", {}, ConfidenceBand.HIGH)
    spec = _assemble(p, None, {})
    assert spec.method == "GET"  # 无 gap 兜底
    assert spec.note and "手工补全" in spec.note
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k "extract_deterministic or assemble" -x -q`
Expected: FAIL（`ImportError`）。

- [ ] **Step 3: 实现 `PartialSpec` + `_extract_deterministic` + `_assemble`**

在 `poc_generator.py` 的 `extract_gn_location` **之后**新增：

```python
@dataclass
class PartialSpec:
    """确定性提取的部分 PoC spec（inj/xss/ssrf 分层组装中间结构）。

    route/witness 任一缺失 → needs_gap_fill=True，归入按 controller 文件分组的 LLM 补缺。
    """
    vuln: Any
    vuln_class: str
    band: ConfidenceBand
    param_name: str | None
    placement: str            # "query" | "body"
    controller_file: str | None
    method: str | None
    path: str | None
    witness: str | None

    @property
    def needs_gap_fill(self) -> bool:
        return not self.method or not self.path or not self.witness


def _extract_deterministic(
    vuln: Any, vuln_class: str, endpoints: dict, band: ConfidenceBand
) -> PartialSpec:
    """从 vuln 确定性提取 PartialSpec（不调 LLM）。缺 route/witness 时 needs_gap_fill=True。"""
    method, path = derive_method_path(vuln)
    param = extract_param_name(getattr(vuln, "source", None))
    gn_param, gn_file, _gn_method = extract_gn_location(getattr(vuln, "source", None))
    if not param and gn_param:
        param = gn_param
    witness = getattr(vuln, "witness_payload", None) or None
    placement = "body" if vuln_class == "ssrf" else "query"
    return PartialSpec(
        vuln=vuln, vuln_class=vuln_class, band=band, param_name=param,
        placement=placement, controller_file=gn_file,
        method=method, path=path, witness=witness,
    )


def _assemble(partial: PartialSpec, gap: dict | None, endpoints: dict) -> HttpRequestSpec:
    """用确定性 partial + LLM gap-fill({http_method,route_path,witness_payload}) 组装最终 spec。

    route 补回后重查 recon endpoints 得 auth_state。无 gap/缺 witness → 骨架 + 标注。
    """
    g = gap or {}
    method = partial.method or (g.get("http_method") or "GET")
    path = partial.path or (g.get("route_path") or "/")
    witness = partial.witness or g.get("witness_payload") or ""
    info = find_endpoint_info(endpoints, path)
    auth_st = derive_auth_state(info)
    spec = HttpRequestSpec(
        method=str(method).upper(), path=path,
        headers=auth_header(auth_st, info), auth_state=auth_st,
        confidence_band=partial.band,
        source_id=getattr(partial.vuln, "ID", ""), vuln_class=partial.vuln_class,
    )
    if not witness:
        spec.note = "请求形态未推断（缺 witness），需手工补全 body/参数"
        return spec
    # 按 vuln_class 决定参数位（对齐既有 build_template_spec 逻辑）
    if partial.vuln_class == "ssrf":
        if _is_open_redirect(partial.vuln):
            param = partial.param_name or "next"
            spec.query = {param: witness}
        else:
            param = getattr(partial.vuln, "vulnerable_parameter", None) or partial.param_name or "url"
            if spec.method == "GET":
                spec.method = "POST"
            spec.body = f"{param}={witness}"
    else:  # injection / xss
        param = partial.param_name or ("id" if partial.vuln_class == "injection" else "q")
        spec.query = {param: witness}
    return spec
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k "extract_deterministic or assemble" -x -q`
Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/services/poc_generator.py packages/core/tests/test_poc_generator.py
git commit -m "feat(core): PoC PartialSpec/_extract_deterministic/_assemble 分层组装

确定性提取参数名/文件/method(缺 route/witness 则 needs_gap_fill);
_assemble 用 LLM gap-fill({method,route,witness})补缺口后模板组装,
route 补回后重查 recon 得 auth_state。为按文件分组批量 LLM 奠基。"
```

---

### Task 4: `_group_by_controller_file` —— 按文件分组 + cap 拆分

把待补 `PartialSpec` 按 controller 文件聚合，每组 cap 上限，超出拆分（回应「一类漏洞太多单次 LLM 易错」）。

**Files:**
- Modify: `packages/core/src/supernova_core/services/poc_generator.py`
- Test: `packages/core/tests/test_poc_generator.py`

**Interfaces:**
- Produces: `_group_by_controller_file(partials: list[PartialSpec], cap: int = 8) -> list[tuple[str | None, list[PartialSpec]]]` —— `[(file_key, [partials...]), ...]`，每个 list 长度 ≤ cap

- [ ] **Step 1: 写失败测试**

追加到 `test_poc_generator.py`：

```python
def _gn_partial(vid, f):
    from supernova_core.services.poc_generator import PartialSpec
    class V:
        ID = vid
    return PartialSpec(vuln=V(), vuln_class="injection", band=ConfidenceBand.HIGH,
                       param_name="p", placement="query", controller_file=f,
                       method=None, path=None, witness=None)


def test_group_by_controller_file_buckets_and_caps():
    from supernova_core.services.poc_generator import _group_by_controller_file
    partials = [
        _gn_partial("A1", "C1.java"), _gn_partial("A2", "C1.java"),
        _gn_partial("B1", "C2.java"),
        _gn_partial("U1", None),  # 无文件 → fallback 桶
    ]
    groups = _group_by_controller_file(partials, cap=8)
    files = sorted(str(f) for f, _ in groups)
    assert files == ["C1.java", "C2.java", "None"]  # None 桶兜底
    c1 = [ps for f, ps in groups if f == "C1.java"][0]
    assert len(c1) == 2


def test_group_by_controller_file_splits_on_cap():
    from supernova_core.services.poc_generator import _group_by_controller_file
    # 同一文件 10 条，cap=4 → 拆成 3 组(4+4+2)
    partials = [_gn_partial(f"X{i}", "Same.java") for i in range(10)]
    groups = _group_by_controller_file(partials, cap=4)
    same = [ps for f, ps in groups if f == "Same.java"]
    assert len(same) == 3  # 4+4+2
    assert len(same[0]) == 4 and len(same[2]) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k group_by_controller_file -x -q`
Expected: FAIL（`ImportError`）。

- [ ] **Step 3: 实现 `_group_by_controller_file`**

在 `poc_generator.py` 的 `_assemble` **之后**新增：

```python
def _group_by_controller_file(
    partials: list["PartialSpec"], cap: int = 8
) -> list[tuple[str | None, list["PartialSpec"]]]:
    """按 controller_file 聚合待补 PartialSpec，每组 ≤ cap，超出按 cap 拆分多次。

    无 controller_file（提取不到）→ fallback 桶 key=None。
    cap 由 env SUPERNOVA_POC_GROUP_CAP 覆盖（默认 8）。
    """
    buckets: dict[str | None, list["PartialSpec"]] = {}
    for p in partials:
        buckets.setdefault(p.controller_file, []).append(p)
    out: list[tuple[str | None, list["PartialSpec"]]] = []
    for f, ps in buckets.items():
        for i in range(0, len(ps), cap):
            out.append((f, ps[i:i + cap]))
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k group_by_controller_file -x -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/services/poc_generator.py packages/core/tests/test_poc_generator.py
git commit -m "feat(core): PoC _group_by_controller_file 按文件分组+cap 拆分

回应'一类漏洞太多单次 LLM 易错':按 controller 文件聚合(同文件 N 条只读 1 次),
cap 默认 8 超出拆分。无文件→fallback 桶。"
```

---

### Task 5: `llm_fill_gaps` + `GAPFILL_OUTPUT_SCHEMA` —— 分组批量补缺

每组（一个 controller 文件）一次轻量 LLM 调用，只返回每条的 `{http_method, route_path, witness_payload}`。

**Files:**
- Modify: `packages/core/src/supernova_core/services/poc_generator.py`
- Test: `packages/core/tests/test_poc_generator.py`

**Interfaces:**
- Consumes: `run_claude_prompt`（既有，模块级名）
- Produces:
  - `GAPFILL_OUTPUT_SCHEMA: dict`
  - `async def llm_fill_gaps(file_key, partials, *, recon_ctx, repo_path, api_key=None, model_tier="medium") -> dict[str, dict]` —— `{ID: {http_method, route_path, witness_payload}}`
  - `async def _batch_fill_gaps(partials, *, endpoints, repo_path, api_key=None, model_tier="medium") -> dict[str, dict]` —— 编排分组 + 逐组调 `llm_fill_gaps`，合并结果（失败的组跳过，其条目无 gap → 后续降级骨架）

- [ ] **Step 1: 写失败测试**

追加到 `test_poc_generator.py`：

```python
async def test_llm_fill_gaps_groups_by_file_and_returns_map(monkeypatch):
    """同文件多条 → 1 次 LLM 调用,返回 {ID: {method,route,witness}}。"""
    import supernova_core.services.poc_generator as mod
    calls = []

    async def fake_run(prompt, **kw):
        calls.append(kw.get("structured_output_schema"))
        return SimpleNamespace(success=True, structured_output={
            "items": [
                {"ID": "INJ-GN-01", "http_method": "POST",
                 "route_path": "/a", "witness_payload": "w1"},
                {"ID": "INJ-GN-02", "http_method": "GET",
                 "route_path": "/b", "witness_payload": "w2"},
            ]}, error=None)
    monkeypatch.setattr(mod, "run_claude_prompt", fake_run)

    partials = [_gn_partial("INJ-GN-01", "C.java"), _gn_partial("INJ-GN-02", "C.java")]
    gapmap = await mod.llm_fill_gaps("C.java", partials, recon_ctx={},
                                     repo_path="/tmp/x")
    assert len(calls) == 1  # 同文件 2 条只 1 次调用
    assert gapmap["INJ-GN-01"]["route_path"] == "/a"
    assert gapmap["INJ-GN-02"]["witness_payload"] == "w2"


async def test_batch_fill_gaps_merges_groups(monkeypatch):
    """2 个文件 → 2 次调用,结果合并;某组失败不影响他组。"""
    import supernova_core.services.poc_generator as mod
    n = {"i": 0}

    async def fake_run(prompt, **kw):
        n["i"] += 1
        if n["i"] == 1:  # 第一组(C1.java)成功
            return SimpleNamespace(success=True, structured_output={
                "items": [{"ID": "A1", "http_method": "POST",
                           "route_path": "/a", "witness_payload": "wa"}]}, error=None)
        # 第二组(C2.java)失败
        return SimpleNamespace(success=False, structured_output=None, error="boom")
    monkeypatch.setattr(mod, "run_claude_prompt", fake_run)

    partials = [_gn_partial("A1", "C1.java"), _gn_partial("B1", "C2.java")]
    gapmap = await mod._batch_fill_gaps(partials, endpoints={}, repo_path="/tmp/x")
    assert gapmap == {"A1": {"http_method": "POST", "route_path": "/a", "witness_payload": "wa"}}
    assert "B1" not in gapmap  # 失败组无 gap → 后续降级骨架


async def test_llm_fill_gaps_exception_returns_empty(monkeypatch):
    import supernova_core.services.poc_generator as mod

    async def boom(prompt, **kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr(mod, "run_claude_prompt", boom)
    partials = [_gn_partial("A1", "C.java")]
    gapmap = await mod.llm_fill_gaps("C.java", partials, recon_ctx={}, repo_path="/tmp/x")
    assert gapmap == {}  # 异常 → 空(降级骨架)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k "llm_fill_gaps or batch_fill_gaps" -x -q`
Expected: FAIL（`ImportError`/`AttributeError`）。

- [ ] **Step 3: 实现 `GAPFILL_OUTPUT_SCHEMA` + `llm_fill_gaps` + `_batch_fill_gaps`**

在 `poc_generator.py` 的 `LLM_REQUEST_SCHEMA` 定义**之后**新增 schema；在 `llm_fill_gap` 函数**之后**新增两个函数：

```python
GAPFILL_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ID": {"type": "string"},
                    "http_method": {"type": ["string", "null"],
                                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", None]},
                    "route_path": {"type": ["string", "null"]},
                    "witness_payload": {"type": ["string", "null"]},
                },
                "required": ["ID"],
            },
        }
    },
    "required": ["items"],
}
```

```python
def _build_gapfill_prompt(file_key: str | None, partials: list["PartialSpec"], recon_ctx: dict) -> str:
    items_desc = json.dumps([
        {"ID": getattr(p.vuln, "ID", ""), "param": p.param_name,
         "method_hint": None, "vuln_class": p.vuln_class,
         "evidence_chain": (getattr(p.vuln, "evidence_chain", None) or "")[:300]}
        for p in partials
    ], ensure_ascii=False)
    file_line = f"Handler file: {file_key}\n" if file_key else "Handler file: unknown\n"
    return (
        f"You are reconstructing HTTP request shapes for confirmed vulnerabilities.\n\n"
        f"{file_line}Read that file and find each handler method's HTTP route "
        f"(@PostMapping / router.get / @app.route …) and a minimal witness payload.\n\n"
        f"Vulnerabilities to fill:\n{items_desc}\n\n"
        f"Recon endpoint context:\n{json.dumps(recon_ctx, ensure_ascii=False)}\n\n"
        f"Output JSON {{\"items\":[{{\"ID\",\"http_method\",\"route_path\","
        f"\"witness_payload\"}}]}}. Output JSON only."
    )


async def llm_fill_gaps(
    file_key: str | None, partials: list["PartialSpec"], *, recon_ctx: dict,
    repo_path: str, api_key: str | None = None, model_tier: str = "medium",
) -> dict[str, dict]:
    """一个 controller 文件组一次 LLM 调用,返回 {ID: {http_method,route_path,witness_payload}}。

    失败/不可用 → 返回 {}(调用方对缺 gap 的条目降级骨架)。
    """
    prompt = _build_gapfill_prompt(file_key, partials, recon_ctx)
    try:
        result = await run_claude_prompt(
            prompt=prompt,
            repo_path=repo_path or "/tmp/poc-gen",
            model_tier=model_tier,
            structured_output_schema=GAPFILL_OUTPUT_SCHEMA,
            api_key=api_key,
            max_turns=int(os.getenv("SUPERNOVA_POC_MAX_TURNS", "10")),
        )
    except Exception:
        return {}
    if not getattr(result, "success", False) or not getattr(result, "structured_output", None):
        return {}
    items = result.structured_output.get("items") or []
    out: dict[str, dict] = {}
    for it in items:
        vid = it.get("ID")
        if vid:
            out[vid] = {
                "http_method": it.get("http_method"),
                "route_path": it.get("route_path"),
                "witness_payload": it.get("witness_payload"),
            }
    return out


async def _batch_fill_gaps(
    partials: list["PartialSpec"], *, endpoints: dict, repo_path: str,
    api_key: str | None = None, model_tier: str = "medium",
) -> dict[str, dict]:
    """编排:分组 + 逐组调 llm_fill_gaps,合并 {ID: gap}。失败的组其条目无 gap(后降级)。"""
    cap = int(os.getenv("SUPERNOVA_POC_GROUP_CAP", "8"))
    groups = _group_by_controller_file(partials, cap=cap)
    gapmap: dict[str, dict] = {}
    for file_key, group_partials in groups:
        recon_ctx = {ep: info for ep, info in endpoints.items()} if endpoints else {}
        try:
            gapmap.update(await llm_fill_gaps(
                file_key, group_partials, recon_ctx=recon_ctx,
                repo_path=repo_path, api_key=api_key, model_tier=model_tier))
        except Exception as exc:  # 单组失败不阻塞其余
            logger.warning("poc: llm_fill_gaps failed for %s: %s", file_key, exc)
    return gapmap
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k "llm_fill_gaps or batch_fill_gaps" -x -q`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/services/poc_generator.py packages/core/tests/test_poc_generator.py
git commit -m "feat(core): PoC llm_fill_gaps 按 controller 文件分组批量补 route+witness

每组(一个文件)1 次轻量 LLM,只返回 {http_method,route_path,witness_payload};
_batch_fill_gaps 编排分组+合并,单组失败不阻塞。把 ~48 次逐条调用降到文件数。"
```

---

### Task 6: 重构 `generate()` 主循环 —— 分层模板优先 + 分组补缺集成

把 inj/xss/ssrf 改为：模板命中（`build_template_spec` 非 None）→ 0ms；否则收集待补 → `_batch_fill_gaps` → `_assemble`。authz/auth 保持既有 `_build_entry` per-item 路径。

**Files:**
- Modify: `packages/core/src/supernova_core/services/poc_generator.py`（`PoCGenerator.generate` 主循环；保留 `_build_entry` 给 authz/auth）
- Test: `packages/core/tests/test_poc_generator.py`

**Interfaces:**
- Consumes: `_extract_deterministic`/`_assemble`/`_batch_fill_gaps`（Task 3/5）、`build_template_spec`/`classify_confidence`/`_build_entry`（既有）
- Produces: 重构后的 `PoCGenerator.generate`（签名不变）

- [ ] **Step 1: 写集成失败测试**

追加到 `test_poc_generator.py`：

```python
async def test_generate_layered_template_first_then_grouped_gapfill(tmp_path, monkeypatch):
    """LLM 轨(有 route+witness)0ms 模板命中,不走 LLM;GitNexus 轨按文件分组批量补缺。"""
    d = tmp_path / "deliverables" / "whitebox"
    d.mkdir(parents=True)
    q = VulnerabilityQueue(vulnerabilities=[
        # LLM 轨:模板命中(有 path + witness)→ 不触发 LLM
        InjectionVulnerability(ID="INJ-VULN-01", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="GET /api/users?id=1", path="GET /api/users?id=1 -> sink",
            witness_payload="1' OR '1'='1", verdict="vulnerable"),
        # GitNexus 轨:无 route/witness → 分组补缺
        InjectionVulnerability(ID="INJ-GN-01", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="payload (src/main/java/x/C.java:m:70)",
            path="payload -> C.java:m", witness_payload=None, verdict="vulnerable"),
        InjectionVulnerability(ID="INJ-GN-02", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="payload (src/main/java/x/C.java:m2:80)",
            path="payload -> C.java:m2", witness_payload=None, verdict="vulnerable"),
    ])
    (d / "injection_exploitation_queue.json").write_text(q.model_dump_json(), encoding="utf-8")
    import supernova_core.services.poc_generator as mod
    calls = []

    async def fake_run(prompt, **kw):
        calls.append(prompt)
        return SimpleNamespace(success=True, structured_output={
            "items": [
                {"ID": "INJ-GN-01", "http_method": "POST", "route_path": "/c1",
                 "witness_payload": "w1"},
                {"ID": "INJ-GN-02", "http_method": "GET", "route_path": "/c2",
                 "witness_payload": "w2"},
            ]}, error=None)
    monkeypatch.setattr(mod, "run_claude_prompt", fake_run)

    out = await PoCGenerator.generate(d, ["injection"], "https://t.example.com",
                                      "whitebox", repo_path="/tmp/x")
    md = out.read_text(encoding="utf-8")
    assert len(calls) == 1  # 2 条 GN 同文件 → 1 次批量调用(LLM 轨未触发 LLM)
    assert "INJ-VULN-01" in md and "INJ-GN-01" in md and "INJ-GN-02" in md
    assert "/api/users" in md   # 模板产
    assert "/c1" in md and "/c2" in md  # gap-fill 产
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py::test_generate_layered_template_first_then_grouped_gapfill -x -q`
Expected: FAIL（当前实现逐条调 LLM → `len(calls)` 会是 2，或 GitNexus 无 witness 触发既有 per-item LLM 路径行为不符）。

- [ ] **Step 3: 重构 `PoCGenerator.generate` 主循环**

把 `generate()` 中**收集 items 之后、渲染之前**的主循环段（当前是 `for i, (vc, v, accepted) in enumerate(items, 1):` 那段逐条 `_build_entry`）替换为下面的分层逻辑。`_build_entry` 保留（authz/auth 仍用）。在 `generate()` 内替换：

```python
        entries: list[tuple[str, Any, HttpRequestSpec | list[HttpRequestSpec]]] = []
        entries_by_idx: dict[int, tuple[str, Any, HttpRequestSpec | list[HttpRequestSpec]]] = {}
        # inj/xss/ssrf 的待补项(模板未命中),收集后按文件分组批量补缺
        gapped: list[tuple[int, "PartialSpec"]] = []

        for i, (vc, v, accepted) in enumerate(items, 1):
            vid = getattr(v, "ID", "?")
            label = f"({i}/{total}) {_POC_CLASS_TAG.get(vc, f'[{vc}]')} {vid}"
            t0 = time.monotonic()
            try:
                if vc in ("authz", "auth"):
                    # authz(成对模板)/auth(量小,上游 §5.3 默认 LLM)保持既有 per-item 路径
                    spec = await PoCGenerator._build_entry(
                        v, vc, host, endpoints, accepted,
                        repo_path=repo_path, api_key=api_key, model_tier=model_tier)
                    dt_ms = int((time.monotonic() - t0) * 1000)
                    if spec is not None:
                        entries_by_idx[i] = (vc, v, spec)
                        await _poc_progress(f"{label}  {format_duration(dt_ms)}")
                    else:
                        await _poc_progress(f"{label}  skip {format_duration(dt_ms)}")
                else:
                    # inj/xss/ssrf:模板优先(0ms);未命中 → 收集待补
                    band = classify_confidence(v, is_accepted=(vid in accepted))
                    template = build_template_spec(v, vc, host, endpoints, band)
                    if template is not None:
                        entries_by_idx[i] = (vc, v, template)
                        await _poc_progress(f"{label}  {format_duration(int((time.monotonic()-t0)*1000))}")
                    else:
                        partial = _extract_deterministic(v, vc, endpoints, band)
                        gapped.append((i, partial))
                        await _poc_progress(f"{label}  待补缺(分组) {format_duration(int((time.monotonic()-t0)*1000))}")
            except Exception as exc:  # 单条失败不阻塞其余
                dt_ms = int((time.monotonic() - t0) * 1000)
                logger.warning("poc: build failed for %s: %s", vid, exc)
                await _poc_progress(f"{label}  — {exc} ({format_duration(dt_ms)})")

        # 分组批量补缺(GitNexus 轨缺 route/witness 的项)
        if gapped:
            await _poc_progress(f"PoC 分组补缺: {len(gapped)} 条待补")
            gapmap = await _batch_fill_gaps(
                [p for _, p in gapped], endpoints=endpoints,
                repo_path=repo_path or "/tmp/poc-gen", api_key=api_key, model_tier=model_tier)
            for i, partial in gapped:
                vid = getattr(partial.vuln, "ID", "?")
                spec = _assemble(partial, gapmap.get(vid), endpoints)
                entries_by_idx[i] = (partial.vuln_class, partial.vuln, spec)
            await _poc_progress(f"PoC 分组补缺完成: {len(gapmap)}/{len(gapped)} 条补回 route+witness")

        entries = [entries_by_idx[i] for i in sorted(entries_by_idx)]
```

> 注意：删除被替换的旧逐条循环（原 `for i, (vc, v, accepted) in enumerate(items, 1):` 到对应 `entries.append` 的整段）。`_build_entry` 方法**保留不动**（authz/auth 仍调）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py::test_generate_layered_template_first_then_grouped_gapfill -x -q`
Expected: PASS。

- [ ] **Step 5: 回归全部 poc 测试**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -x -q`
Expected: PASS（含 `test_generate_llm_failure_degrades_gracefully` auth 路径、`test_generate_llm_dict_body_does_not_crash`、空表/占位符/accepted_ids 等）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/services/poc_generator.py packages/core/tests/test_poc_generator.py
git commit -m "feat(core): PoC generate 主循环分层模板优先+分组补缺(inj/xss/ssrf)

模板命中(build_template_spec 非 None)→0ms;否则收集待补→_batch_fill_gaps
按文件分组批量补 route+witness→_assemble 组装。authz(成对模板)/auth(量小)
保持既有 _build_entry per-item 路径。LLM 调用 ~48→文件数(个位数)。"
```

---

### Task 7: Fix A —— workflow 层 try/except 硬保 §8 非阻塞契约

PoC activity timeout/ActivityError 绝不让 workflow FAILED。两轨 workflow 各包一层。

**Files:**
- Modify: `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py:601-606`
- Modify: `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py:410-415`
- Test: `packages/whitebox/tests/test_retry_policy_coverage.py`
- Test: `packages/blackbox/tests/test_retry_policy_coverage.py`

**Interfaces:**
- 无新公开接口；行为契约：`execute_activity(generate_poc_report)` 抛任何异常（含 `ActivityError`）→ workflow 不 FAILED。

- [ ] **Step 1: 写白盒 AST 锚点失败测试**

追加到 `packages/whitebox/tests/test_retry_policy_coverage.py`：

```python
def test_poc_activity_call_is_wrapped_in_try():
    """Fix A(§8 契约硬化):generate_poc_report 的 execute_activity 必须在 try 块内,
    否则 Temporal start_to_close_timeout 抛 ActivityError 会击穿 activity 内部
    try/except(那是 Python 异常,抓不到 runtime cancel)→ workflow FAILED。
    sentinel_dashboard 2026-07-22 实测回归。"""
    source = WORKFLOW_FILE.read_text()
    tree = ast.parse(source)
    found, in_try = False, False

    def visit(node, enclosing_try):
        nonlocal found, in_try
        for child in ast.iter_child_nodes(node):
            child_enclosing = enclosing_try or isinstance(node, ast.Try)
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "execute_activity"
                    and child.args
                    and isinstance(child.args[0], ast.Attribute)
                    and child.args[0].attr == "generate_poc_report"):
                found = True
                if enclosing_try or isinstance(node, ast.Try):
                    in_try = True
            visit(child, child_enclosing)

    visit(tree, False)
    assert found, "找不到 generate_poc_report execute_activity 调用 — 锚点接线坏了"
    assert in_try, (
        "generate_poc_report 的 execute_activity 未包在 try 内 — "
        "PoC timeout 会击穿 §8 非阻塞契约致 workflow FAILED"
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_retry_policy_coverage.py::test_poc_activity_call_is_wrapped_in_try -x -q`
Expected: FAIL（`assert in_try`——当前裸 `await execute_activity(...)`）。

- [ ] **Step 3: 白盒 workflow 包 try/except**

修改 `packages/whitebox/src/supernova_whitebox/pipeline/workflows.py`，把（第 601-606 行附近）：

```python
            self._state.current_agent = "generate-poc-report"
            await workflow.execute_activity(
                activities.generate_poc_report, act_input,
                start_to_close_timeout=timedelta(minutes=20),
                retry_policy=retry_for("poc"),
            )
            self._state.current_agent = None
```

替换为：

```python
            self._state.current_agent = "generate-poc-report"
            try:
                # §8 契约硬化:PoC 是非关键报告增强,timeout/ActivityError 绝不阻塞主流程。
                # activity 内部 try/except 抓不到 Temporal start_to_close_timeout(runtime
                # cancel 非 Python 异常),须在 workflow 层兜底(sentinel_dashboard 2026-07-22 回归)。
                await workflow.execute_activity(
                    activities.generate_poc_report, act_input,
                    start_to_close_timeout=timedelta(minutes=20),
                    retry_policy=retry_for("poc"),
                )
            except Exception:  # noqa: BLE001 — PoC 任何失败(含 ActivityError)只降级
                pass
            finally:
                self._state.current_agent = None
```

- [ ] **Step 4: 跑白盒测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/whitebox/tests/test_retry_policy_coverage.py -x -q`
Expected: PASS（含新锚点 + 既有 retry_policy 锚点）。

- [ ] **Step 5: 写黑盒 AST 锚点测试**

追加到 `packages/blackbox/tests/test_retry_policy_coverage.py`（同样函数；注意该文件的 `WORKFLOW_FILE` 指向 blackbox workflows.py）：

```python
def test_poc_activity_call_is_wrapped_in_try():
    """Fix A(§8 契约硬化):generate_poc_report execute_activity 必须在 try 内,
    防 PoC timeout 击穿非阻塞契约致 workflow FAILED(同 whitebox)。"""
    source = WORKFLOW_FILE.read_text()
    tree = ast.parse(source)
    found, in_try = False, False

    def visit(node, enclosing_try):
        nonlocal found, in_try
        for child in ast.iter_child_nodes(node):
            child_enclosing = enclosing_try or isinstance(node, ast.Try)
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "execute_activity"
                    and child.args
                    and isinstance(child.args[0], ast.Attribute)
                    and child.args[0].attr == "generate_poc_report"):
                found = True
                if enclosing_try or isinstance(node, ast.Try):
                    in_try = True
            visit(child, child_enclosing)

    visit(tree, False)
    assert found, "找不到 generate_poc_report execute_activity 调用 — 锚点接线坏了"
    assert in_try, "generate_poc_report execute_activity 未包在 try 内 — PoC timeout 会击穿 §8 契约"
```

- [ ] **Step 6: 跑黑盒测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_retry_policy_coverage.py::test_poc_activity_call_is_wrapped_in_try -x -q`
Expected: FAIL（`assert in_try`）。

- [ ] **Step 7: 黑盒 workflow 包 try/except**

修改 `packages/blackbox/src/supernova_blackbox/pipeline/workflows.py`（第 410-415 行附近），把：

```python
            await workflow.execute_activity(
                activities.generate_poc_report, act_input,
                start_to_close_timeout=timedelta(minutes=20),
                retry_policy=retry_for("poc"),
            )
```

替换为：

```python
            try:
                # §8 契约硬化:PoC 非关键报告增强,timeout/ActivityError 绝不阻塞主流程
                # (activity 内 try/except 抓不到 Temporal runtime cancel)。
                await workflow.execute_activity(
                    activities.generate_poc_report, act_input,
                    start_to_close_timeout=timedelta(minutes=20),
                    retry_policy=retry_for("poc"),
                )
            except Exception:  # noqa: BLE001
                pass
```

- [ ] **Step 8: 跑黑盒测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/blackbox/tests/test_retry_policy_coverage.py -x -q`
Expected: PASS。

- [ ] **Step 9: Commit**

```bash
cd /root/shannon-py && git add packages/whitebox/src/supernova_whitebox/pipeline/workflows.py packages/blackbox/src/supernova_blackbox/pipeline/workflows.py packages/whitebox/tests/test_retry_policy_coverage.py packages/blackbox/tests/test_retry_policy_coverage.py
git commit -m "fix(pipeline): PoC activity 包 try/except 硬保 §8 非阻塞契约(Fix A)

sentinel_dashboard 2026-07-22:PoC 20min timeout×3 抛 ActivityError 击穿
activity 内 try/except(抓不到 Temporal runtime cancel)致 workflow FAILED。
两轨 workflow 层兜底:PoC 任何失败只降级,主报告不受影响。加 AST 锚点防回归。"
```

---

### Task 8: Fix B —— `.poc_checkpoint.json` 断点续传

retry 从断点续跑而非从零重来。sidecar 存已完成 `{ID: {vuln_class, spec}}`，启动读、每项解决后增量原子写。

**Files:**
- Modify: `packages/core/src/supernova_core/services/poc_generator.py`（`PoCGenerator.generate` 读/写 checkpoint）
- Test: `packages/core/tests/test_poc_generator.py`

**Interfaces:**
- 产生 sidecar：`deliverables_dir / ".poc_checkpoint.json"`，格式 `{"version":1,"track":<str>,"completed":{<ID>: {"vuln_class":<str>,"spec":<serialized HttpRequestSpec dict>}}}`
- 无新公开函数（checkpoint 逻辑内联在 `generate`）。

- [ ] **Step 1: 写失败测试**

追加到 `test_poc_generator.py`：

```python
async def test_generate_checkpoint_resumes_skipping_done(tmp_path, monkeypatch):
    """已有 checkpoint 的条目跳过(不重跑 LLM/模板),只补未完成项。"""
    d = tmp_path / "deliverables" / "whitebox"
    d.mkdir(parents=True)
    q = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(ID="INJ-GN-01", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="payload (src/x/C.java:m:70)", path="payload -> C.java:m",
            witness_payload=None, verdict="vulnerable"),
        InjectionVulnerability(ID="INJ-GN-02", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="payload (src/x/C.java:m2:80)", path="payload -> C.java:m2",
            witness_payload=None, verdict="vulnerable"),
    ])
    (d / "injection_exploitation_queue.json").write_text(q.model_dump_json(), encoding="utf-8")
    # 预置 checkpoint:INJ-GN-01 已完成
    from supernova_core.services.poc_generator import _POC_CHECKPOINT_FILENAME
    ckpt = {"version": 1, "track": "whitebox", "completed": {
        "INJ-GN-01": {"vuln_class": "injection", "spec": {
            "method": "POST", "path": "/done", "query": {"payload": "DONE"},
            "headers": {}, "body": None, "auth_state": "unknown",
            "confidence_band": "suspected", "source_id": "INJ-GN-01",
            "vuln_class": "injection", "note": None, "steps": None}}}}
    (d / _POC_CHECKPOINT_FILENAME).write_text(json.dumps(ckpt), encoding="utf-8")

    import supernova_core.services.poc_generator as mod
    seen_ids = []

    async def fake_run(prompt, **kw):
        seen_ids.append("called")
        return SimpleNamespace(success=True, structured_output={
            "items": [{"ID": "INJ-GN-02", "http_method": "GET",
                       "route_path": "/c2", "witness_payload": "w2"}]}, error=None)
    monkeypatch.setattr(mod, "run_claude_prompt", fake_run)

    out = await PoCGenerator.generate(d, ["injection"], "https://t.example.com",
                                      "whitebox", repo_path="/tmp/x")
    md = out.read_text(encoding="utf-8")
    assert "/done" in md           # checkpoint 复用
    assert "/c2" in md             # 本轮补的
    assert len(seen_ids) == 1      # 只 1 次分组调用(跳过已完成的 INJ-GN-01)


async def test_generate_checkpoint_corrupt_starts_fresh(tmp_path, monkeypatch):
    """checkpoint 损坏 → 当作无 checkpoint 从头跑,不报错。"""
    d = tmp_path / "deliverables" / "whitebox"
    d.mkdir(parents=True)
    q = VulnerabilityQueue(vulnerabilities=[
        InjectionVulnerability(ID="INJ-GN-01", vulnerability_type="SQLi",
            externally_exploitable=True, confidence="high",
            source="GET /a?id=1", witness_payload="x", verdict="vulnerable"),
    ])
    (d / "injection_exploitation_queue.json").write_text(q.model_dump_json(), encoding="utf-8")
    from supernova_core.services.poc_generator import _POC_CHECKPOINT_FILENAME
    (d / _POC_CHECKPOINT_FILENAME).write_text("{NOT JSON", encoding="utf-8")  # 损坏
    out = await PoCGenerator.generate(d, ["injection"], "https://t.example.com", "whitebox")
    assert out.exists()  # 不报错,正常产出
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k checkpoint -x -q`
Expected: FAIL（`ImportError: cannot import name '_POC_CHECKPOINT_FILENAME'`）。

- [ ] **Step 3: 实现 checkpoint 读写 + 集成进 `generate`**

在 `poc_generator.py` 的 `_POC_FILENAME = "exploitable_poc_collection.md"` **下方**新增：

```python
_POC_CHECKPOINT_FILENAME = ".poc_checkpoint.json"


def _ckpt_path(deliverables_dir: Path) -> Path:
    return deliverables_dir / _POC_CHECKPOINT_FILENAME


def _load_checkpoint(deliverables_dir: Path) -> dict:
    """读 sidecar checkpoint。损坏/缺失 → 返回空(从头跑,降级不报错)。"""
    p = _ckpt_path(deliverables_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("completed", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _spec_to_ckpt(spec: HttpRequestSpec | list[HttpRequestSpec]) -> Any:
    """HttpRequestSpec(或 list)序列化为 checkpoint 可存 dict。"""
    if isinstance(spec, list):
        return [_spec_to_ckpt(s) for s in spec]
    return {
        "method": spec.method, "path": spec.path, "query": spec.query,
        "headers": spec.headers, "body": spec.body, "auth_state": spec.auth_state.value,
        "confidence_band": spec.confidence_band.value, "source_id": spec.source_id,
        "vuln_class": spec.vuln_class, "note": spec.note, "steps": None,
    }


def _spec_from_ckpt(raw: dict) -> HttpRequestSpec:
    return HttpRequestSpec(
        method=raw.get("method", "GET"), path=raw.get("path", "/"),
        query=raw.get("query", {}), headers=raw.get("headers", {}),
        body=raw.get("body"), auth_state=AuthState(raw.get("auth_state", "unknown")),
        confidence_band=ConfidenceBand(raw.get("confidence_band", "suspected")),
        source_id=raw.get("source_id", ""), vuln_class=raw.get("vuln_class", ""),
        note=raw.get("note"),
    )


def _write_checkpoint(deliverables_dir: Path, track: str,
                      completed: dict[str, dict]) -> None:
    """原子写 checkpoint(临时文件 + os.replace)。"""
    p = _ckpt_path(deliverables_dir)
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(
            {"version": 1, "track": track, "completed": completed}, ensure_ascii=False),
            encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        logger.warning("poc: checkpoint write failed (non-blocking)")
```

然后在 `generate()` 内，**在 `for i, (vc, v, accepted) in enumerate(items, 1):` 循环之前**加载 checkpoint 并标记已完成项；在每项 resolve 后增量写。具体改动（在 Task 6 重构后的 `generate` 基础上）：

(a) 在 `entries_by_idx: dict[...] = {}` 行**之后**加：

```python
        # Fix B:断点续传 — 读 checkpoint,reuse 已完成项,retry 不从零重来
        ckpt_completed = _load_checkpoint(deliverables_dir)
        ckpt_done_ids = set(ckpt_completed.keys())
```

(b) 在主循环 `for i, (vc, v, accepted) in enumerate(items, 1):` 的 `try:` 块**最前面**加跳过逻辑：

```python
            try:
                vid = getattr(v, "ID", "?")
                if vid in ckpt_done_ids and vid in ckpt_completed:
                    raw = ckpt_completed[vid]
                    spec = _spec_from_ckpt(raw["spec"]) if isinstance(raw, dict) else None
                    if spec is not None:
                        entries_by_idx[i] = (vc, v, spec)
                        await _poc_progress(
                            f"({i}/{total}) {_POC_CLASS_TAG.get(vc, f'[{vc}]')} {vid}  复用(checkpoint)")
                        continue
                label = f"({i}/{total}) {_POC_CLASS_TAG.get(vc, f'[{vc}]')} {vid}"
                t0 = time.monotonic()
```

> 即：把原 `vid = getattr(v, "ID", "?")` / `label = ...` / `t0 = ...` 三行**移到** checkpoint 跳过逻辑之后（上面已含），并在跳过命中时 `continue`。

(c) 在主循环**每个 resolve 分支**（authz/auth 的 `_build_entry` 成功、inj/xss/ssrf 模板命中）以及分组补缺循环结束后，把新完成的 entry 写入 checkpoint。在主循环 `except Exception` **之前**（即 try 块内、各 resolve 分支设置 `entries_by_idx[i]` 之后）统一加一个 checkpoint 写入点——最简做法：在 try 块末尾、循环体最后加：

```python
                # 增量写 checkpoint(模板/authz/auth 路径)
                if i in entries_by_idx:
                    _vc, _v, _spec = entries_by_idx[i]
                    ckpt_completed[getattr(_v, "ID", str(i))] = {
                        "vuln_class": _vc, "spec": _spec_to_ckpt(_spec)}
                    _write_checkpoint(deliverables_dir, track, ckpt_completed)
```

> 注意：此段在 try 块内、`except Exception` 之前；checkpoint 跳过的分支已 `continue` 不会到这。gapped（待补）项此处尚未 resolve（无 entries_by_idx[i]），不写——它们在下方分组补缺后统一写。

(d) 在分组补缺循环（`for i, partial in gapped:`）**之后**、`await _poc_progress(f"PoC 分组补缺完成...")` **之前**加：

```python
            for i, partial in gapped:
                vid = getattr(partial.vuln, "ID", "?")
                spec = _assemble(partial, gapmap.get(vid), endpoints)
                entries_by_idx[i] = (partial.vuln_class, partial.vuln, spec)
                ckpt_completed[vid] = {
                    "vuln_class": partial.vuln_class, "spec": _spec_to_ckpt(spec)}
            _write_checkpoint(deliverables_dir, track, ckpt_completed)
```

> 即把 Task 6 里那段 `for i, partial in gapped:` 内的 `_assemble`+`entries_by_idx[i]=...` 保留，并在循环后追加 checkpoint 写入（上面合并展示）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -k checkpoint -x -q`
Expected: PASS（2 passed）。

- [ ] **Step 5: 回归全部 poc 测试**

Run: `cd /root/shannon-py && python -m pytest packages/core/tests/test_poc_generator.py -x -q`
Expected: PASS（全部，含 Task 6 集成测试 + 既有）。

- [ ] **Step 6: Commit**

```bash
cd /root/shannon-py && git add packages/core/src/supernova_core/services/poc_generator.py packages/core/tests/test_poc_generator.py
git commit -m "feat(core): PoC .poc_checkpoint.json 断点续传(Fix B)

retry 从断点续跑而非从零重来:sidecar 存已完成 {ID:{vuln_class,spec}},
启动读复用、每项 resolve 后原子增量写。损坏→从头跑降级不报错。
配合 Fix A:即使 workflow 不 fail,PoC 也能在 retry 中逐步推进产全。"
```

---

## Self-Review（计划 vs spec 覆盖核对）

**Spec 覆盖：**
- §2.4 str items bug → **Task 1** ✓
- §4.2 确定性提取（extract_gn_location / 参数名 / 文件 / 方法）→ **Task 2** ✓
- §4.2 PartialSpec + §4.5 _assemble（含 route 补回后重查 recon auth_state）→ **Task 3** ✓
- §4.3 按 controller 文件分组 + cap → **Task 4** ✓
- §4.4 GAPFILL_OUTPUT_SCHEMA + 分组批量 LLM 只补 route+witness → **Task 5** ✓
- §4.1/§4.5 分层模板优先集成（authz/auth 保持既有路径）→ **Task 6** ✓
- §5.1 Fix A workflow 层兜底（§8 硬化，两轨）→ **Task 7** ✓
- §5.2 Fix B checkpoint 断点续传 → **Task 8** ✓
- §5.3 timeout 维持 20min（无需任务，配置不变，Global Constraints 已注明）✓
- §6 str bug（同 Task 1）✓
- §7 降级矩阵：LLM 失败→骨架（Task 3 `_assemble` gap=None 分支 + Task 5 `_batch_fill_gaps` 单组失败不阻塞）✓；checkpoint 损坏→从头（Task 8）✓
- §9 不变量：不动双轨/verdict/merger（所有任务限 poc_generator + 两轨 workflow PoC 包裹）✓；产物格式不变 ✓

**Placeholder 扫描：** 无 TBD/TODO；所有步骤含完整代码与确切命令。✓

**类型一致性核对：**
- `extract_gn_location` 返回 `(str|None, str|None, str|None)` —— Task 2 定义、Task 3 `_extract_deterministic` 消费一致 ✓
- `PartialSpec` 字段（`controller_file`/`method`/`path`/`witness`/`needs_gap_fill`）—— Task 3 定义、Task 4 `_group_by_controller_file` 用 `controller_file`、Task 5 `_build_gapfill_prompt` 用 `vuln.ID`/`param_name`、Task 6 集成用 `needs_gap_fill` 隐含（经 `build_template_spec is None` 判定）一致 ✓
- `_assemble(partial, gap, endpoints)` —— Task 3 定义、Task 6 调 `_assemble(partial, gapmap.get(vid), endpoints)` 一致 ✓
- `_batch_fill_gaps(partials, *, endpoints, repo_path, ...)` —— Task 5 定义、Task 6 调用签名一致 ✓
- `_POC_CHECKPOINT_FILENAME` —— Task 8 定义并测试导入一致 ✓
- `HttpRequestSpec` 既有字段（`method/path/query/headers/body/auth_state/confidence_band/source_id/vuln_class/note/steps`）—— `_spec_to_ckpt`/`_spec_from_ckpt` 一致 ✓

无遗漏。计划可执行。
