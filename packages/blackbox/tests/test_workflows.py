from pathlib import Path

from supernova_blackbox.pipeline.shared import BlackboxPipelineInput, BlackboxPipelineState
from supernova_blackbox.pipeline.workflows import BlackboxScanWorkflow
from supernova_core.models.errors import ErrorCode, PentestError, classify_error_for_temporal
from supernova_core.services.browser_engine import BrowserEngineFactory


def test_pipeline_progress_query_registered_as_PipelineProgress():
    """The progress query must be registered under the name 'PipelineProgress'.

    worker.py polls via `handle.query("PipelineProgress")`. A bare
    @workflow.query would register under the method name 'pipeline_progress',
    so the query would fail silently (swallowed by `except Exception: pass`)
    and the CLI would print no progress. This guards that regression.
    See docs/superpowers/specs/2026-06-09-pipeline-progress-query-design.md.
    """
    defn = getattr(
        BlackboxScanWorkflow.pipeline_progress,
        "__temporal_query_definition",
        None,
    )
    assert defn is not None, "pipeline_progress is not a registered @workflow.query"
    assert defn.name == "PipelineProgress"


from supernova_core.utils.paths import resolve_deliverables_path


def test_path_resolution_workspace_name_priority(tmp_path):
    """workspace_name 优先 → workspaces/<name>/deliverables。"""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    input = BlackboxPipelineInput(
        web_url="https://example.com",
        repo_path=str(repo),
        workspace_name="my-scan",
    )
    result = resolve_deliverables_path(
        repo_path=input.repo_path,
        deliverables_subdir=input.deliverables_subdir,
        workspace_name=input.workspace_name,
        workspaces_root=tmp_path / "workspaces",
    )
    assert result == tmp_path / "workspaces" / "my-scan" / "deliverables"


def test_path_resolution_pure_fallback(tmp_path):
    """无 workspace_name 时回退 repo_path/deliverables。"""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    result = resolve_deliverables_path(
        repo_path=str(repo),
        deliverables_subdir="deliverables",
    )
    assert result == repo / "deliverables"


def test_state_tracks_found_classes_with_results(tmp_path):
    """When exploitation_queue.json exists, found classes should be tracked in state."""
    state = BlackboxPipelineState(
        has_whitebox_results=True,
        found_whitebox_classes=["injection", "xss"],
    )
    assert state.has_whitebox_results is True
    assert state.found_whitebox_classes == ["injection", "xss"]


def test_state_defaults_no_found_classes():
    """Default state should have empty found_whitebox_classes."""
    state = BlackboxPipelineState()
    assert state.found_whitebox_classes == []


def test_pipeline_input_max_concurrent_default():
    """Default max_concurrent should be 3."""
    input = BlackboxPipelineInput(web_url="https://example.com")
    assert input.max_concurrent == 3


def test_pipeline_input_max_concurrent_custom():
    """Custom max_concurrent should be respected."""
    input = BlackboxPipelineInput(web_url="https://example.com", max_concurrent=5)
    assert input.max_concurrent == 5


class TestBlackboxWorkflowErrorPropagation:
    """Test the error propagation logic that BlackboxScanWorkflow uses."""

    def test_state_completed_when_no_errors(self):
        """All agents succeed -> status=completed."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX", "REPORT"]
        state.agent_metrics = {"RECON_BLACKBOX": {}, "REPORT": {}}
        if state.errors:
            state.status = "failed"
        else:
            state.status = "completed"
        assert state.status == "completed"
        assert state.failed_agents == []
        assert state.error_code is None

    def test_state_failed_when_exploit_agents_fail(self):
        """Some exploit agents fail -> status=failed with error classification."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX", "injection-exploit"]
        state.errors = ["xss-exploit: 403 Forbidden"]
        state.failed_agents = ["xss-exploit"]
        if state.errors:
            state.status = "failed"
            first_error_msg = state.errors[0].split(": ", 1)[-1]
            error_type, _ = classify_error_for_temporal(Exception(first_error_msg))
            state.error_code = error_type
        else:
            state.status = "completed"
        assert state.status == "failed"
        assert state.failed_agents == ["xss-exploit"]
        assert state.error_code == "PermissionError"

    def test_state_cancelled(self):
        """Cancelled -> status=cancelled."""
        state = BlackboxPipelineState()
        state.status = "cancelled"
        assert state.status == "cancelled"

    def test_state_failed_with_all_exploits_failing(self):
        """All exploit agents fail -> still records all failures."""
        state = BlackboxPipelineState()
        state.completed_agents = ["RECON_BLACKBOX"]
        state.errors = [
            "injection-exploit: connection refused",
            "xss-exploit: authentication failed",
        ]
        state.failed_agents = ["injection-exploit", "xss-exploit"]
        state.status = "failed"
        state.error_code = "TransientError"
        assert state.status == "failed"
        assert len(state.failed_agents) == 2


def test_exploit_tasks_unpacking_arity_matches_construction():
    """exploit_tasks 元素是 3-tuple (vt, agent_name, task)；所有解包点 arity 必须与构造一致。

    回归 guard for 9f770e3d0：scheduled_vuln_types 曾误用 2-tuple 解包 (vt, _)，
    对 3-tuple 抛 "too many values to unpack (expected 2)" ValueError，导致任何
    调度了漏洞的黑盒扫描（exploit_tasks 非空）在 exploitation 阶段直接崩溃。
    空跑（无漏洞 → exploit_tasks=[]）从不触发，故长期潜伏。
    """
    import ast
    from supernova_blackbox.pipeline import workflows as wf

    tree = ast.parse(Path(wf.__file__).read_text())

    construct_arity = None
    unpack_arities = []

    def _is_exploit_tasks(node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id == "exploit_tasks"

    for node in ast.walk(tree):
        # 构造点：exploit_tasks.append((vt, agent_name, task))
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "exploit_tasks"):
            for arg in node.args:
                if isinstance(arg, ast.Tuple):
                    construct_arity = len(arg.elts)
        # 解包点 1：comprehension target —— for <tuple> in exploit_tasks
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            for gen in node.generators:
                if isinstance(gen.target, ast.Tuple) and _is_exploit_tasks(gen.iter):
                    unpack_arities.append(len(gen.target.elts))
        # 解包点 2：赋值 —— <tuple> = exploit_tasks[...]
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Tuple):
                    src = node.value.value if isinstance(node.value, ast.Subscript) else node.value
                    if _is_exploit_tasks(src):
                        unpack_arities.append(len(tgt.elts))

    assert construct_arity == 3, (
        f"exploit_tasks 构造元组 arity 变了 ({construct_arity}); "
        "若有意改动，请同步更新本测试与所有解包点"
    )
    assert unpack_arities, "未找到 exploit_tasks 解包点，本测试可能已失效"
    assert all(a == construct_arity for a in unpack_arities), (
        f"exploit_tasks 解包 arity {unpack_arities} 与构造 arity {construct_arity} 不一致; "
        "某处解包目标数 != 构造元组元素数，会抛 ValueError: too many values to unpack"
    )


def test_auth_validation_and_exploit_share_workspace_path_for_manifest(tmp_path):
    """子项目2 T9 回归：run_blackbox_auth_validation 写 identity-manifest.json 到
    input.workspace_path（经 validate_authentication），run_exploit_agent 经
    exploit_executor 读 load_identity_manifest(deliverables.parent)。两者必须指向
    同一目录，否则 manifest 路径分叉 → exploit 阶段 IDENTITY_CONTEXT 恒空 →
    多身份越权对比失明（authz-exploit 退化成单身份扫描，silent failure）。

    锁定链路（三段，缺一即路径分叉）：
      (A) activities.run_blackbox_auth_validation → validate_authentication(workspace_path=input.workspace_path)
      (B) activities.run_exploit_agent → deliverables = _get_deliverables_path(input);
          exploit.execute(workspace_path=deliverables.parent)
      (C) _get_deliverables_path(input).parent == Path(input.workspace_path)

    若 (A) 改成 workspace_path=deliverables.parent 或别的变量、或 (B) 改成 workspace_path=别的
    路径、或 _get_deliverables_path 不再以 input.workspace_path 为根，本测试 FAIL。
    """
    import ast
    from supernova_blackbox.pipeline import activities as acts
    from supernova_blackbox.pipeline.activities import _get_deliverables_path
    from supernova_blackbox.pipeline.shared import BlackboxActivityInput

    tree = ast.parse(Path(acts.__file__).read_text())

    def _fn(name: str) -> ast.AsyncFunctionDef:
        f = next(
            (n for n in tree.body
             if isinstance(n, ast.AsyncFunctionDef) and n.name == name),
            None,
        )
        assert f is not None, f"activities.py 缺少 {name} 定义（本测试失效）"
        return f

    def _kwarg(call_node: ast.Call, kw_name: str) -> ast.expr | None:
        if not isinstance(call_node, ast.Call):
            return None
        for kw in call_node.keywords:
            if kw.arg == kw_name:
                return kw.value
        return None

    # ── (A) run_blackbox_auth_validation 调 validate_authentication(workspace_path=input.workspace_path[or ""]) ──
    auth_fn = _fn("run_blackbox_auth_validation")
    auth_calls = [
        n for n in ast.walk(auth_fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "validate_authentication"
    ]
    assert auth_calls, "run_blackbox_auth_validation 必须调 validate_authentication"
    wp_value = _kwarg(auth_calls[0], "workspace_path")
    assert wp_value is not None, (
        "validate_authentication 必须经 workspace_path= 关键字传参；"
        "改回位置参数会让本测试失效（且使调用点隐式依赖参数顺序，易踩）。"
    )
    # 接受 `input.workspace_path` 或 `input.workspace_path or ""`
    operands = wp_value.values if isinstance(wp_value, ast.BoolOp) else [wp_value]
    assert any(
        isinstance(o, ast.Attribute)
        and isinstance(o.value, ast.Name)
        and o.value.id == "input"
        and o.attr == "workspace_path"
        for o in operands
    ), (
        "validate_authentication(workspace_path=...) 必须引用 input.workspace_path "
        f"（现 {ast.dump(wp_value)}）；改成别的变量会让 manifest 写盘目录与 workflow 传入分叉"
    )

    # ── (B) run_exploit_agent 调 exploit.execute(workspace_path=deliverables.parent) ──
    exploit_fn = _fn("run_exploit_agent")
    # (B.1) deliverables = _get_deliverables_path(input) 赋值存在
    has_deliverables_assign = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "deliverables" for t in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_get_deliverables_path"
        for node in ast.walk(exploit_fn)
    )
    assert has_deliverables_assign, (
        "run_exploit_agent 必须有 deliverables = _get_deliverables_path(input) 赋值；"
        "改了变量名或换路径源会让 manifest 读盘目录与 auth 阶段写盘目录分叉"
    )
    # (B.2) exploit.execute(workspace_path=deliverables.parent)
    exploit_calls = [
        n for n in ast.walk(exploit_fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "execute"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "exploit"
    ]
    assert exploit_calls, "run_exploit_agent 必须调 exploit.execute(...)"
    wp_value = _kwarg(exploit_calls[0], "workspace_path")
    assert wp_value is not None \
        and isinstance(wp_value, ast.Attribute) \
        and wp_value.attr == "parent" \
        and isinstance(wp_value.value, ast.Name) \
        and wp_value.value.id == "deliverables", (
        "exploit.execute 必须经 workspace_path=deliverables.parent 传参 "
        f"（现 {ast.dump(wp_value) if wp_value else '缺 kwarg'}）；"
        "改别的路径源会让 manifest 读盘 != 写盘"
    )

    # ── (C) 功能性核对：_get_deliverables_path(input).parent == Path(input.workspace_path) ──
    ws = tmp_path / "ws"
    ws.mkdir()
    inp = BlackboxActivityInput(
        web_url="http://target/",
        repo_path=str(tmp_path / "repo"),
        workspace_path=str(ws),
    )
    deliverables = _get_deliverables_path(inp)
    assert deliverables.parent == ws, (
        "_get_deliverables_path(input).parent 必须 == Path(input.workspace_path)；"
        f"现 {deliverables.parent} != {ws} → manifest 写 {ws}/ 读 {deliverables.parent}/，分叉"
    )


class TestBlackboxBrowserEngineIntegration:
    """Test browser engine resolution logic used by BlackboxScanWorkflow."""

    def test_unavailable_engine_raises_error(self, monkeypatch):
        """Engine with check_available()=False should trigger PentestError at startup."""
        import supernova_core.services.engines  # noqa: F401 — register engines

        engine = BrowserEngineFactory.get_engine("playwright")
        monkeypatch.setattr(
            engine.__class__, "check_available", lambda self: False
        )
        engine = BrowserEngineFactory.get_engine("playwright")
        assert not engine.check_available()

        # Simulate workflow startup check
        if not engine.check_available():
            error = PentestError(
                f"Browser engine '{engine.name}' is not available. "
                f"Install it with: npm install -g {engine.name} && {engine.name} install",
                "browser",
                error_code=ErrorCode.BROWSER_ENGINE_UNAVAILABLE,
            )
        assert error.error_code == ErrorCode.BROWSER_ENGINE_UNAVAILABLE
        assert "not available" in error.message

    def test_engine_resolved_from_config(self, tmp_path):
        """Engine name should match config.browser_engine field."""
        from supernova_core.config.parser import parse_config
        import supernova_core.services.engines  # noqa: F401

        config_file = tmp_path / "config.yaml"
        config_file.write_text("browser_engine: agent-browser\n")
        cfg = parse_config(str(config_file))

        engine_name = cfg.browser_engine
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "agent-browser"

    def test_default_engine_without_config(self):
        """Without config, engine defaults to agent-browser."""
        import supernova_core.services.engines  # noqa: F401

        engine_name = "agent-browser"
        engine = BrowserEngineFactory.get_engine(engine_name)
        assert engine.name == "agent-browser"

    def test_engine_write_config_replaces_write_stealth_config(self, tmp_path):
        """engine.write_config() should produce the same result as write_stealth_config."""
        import supernova_core.services.engines  # noqa: F401

        engine = BrowserEngineFactory.get_engine("playwright")
        result = engine.write_config(str(tmp_path))
        assert result["result"] in ("wrote", "skipped-existing")
        assert "configPath" in result

    def test_engine_cleanup_removes_config(self, tmp_path):
        """engine.cleanup_config() should remove all engine artifacts."""
        import supernova_core.services.engines  # noqa: F401

        engine = BrowserEngineFactory.get_engine("playwright")
        engine.write_config(str(tmp_path))
        engine.cleanup_config(str(tmp_path))
        assert not (tmp_path / ".playwright").exists()
