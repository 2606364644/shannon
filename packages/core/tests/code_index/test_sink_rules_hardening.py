"""spec 2026-08-21 gitnexus 轨关轨兜底加固 — 规则组(修复点 C/D)测试。

- C: ts-eval slot generic→cmd_argument(eval flow 才能路由进 injection builder 的
  _INJECTION_SLOTS;对齐同类 ts-child-process-exec)。
- D: 新增 ts-res-render 服务端模板渲染 XSS 规则(Express res.render,污点在
  arg_index=1 的 locals 对象;autoescape 上下文交 chain_verdict 判定)。

NodeGoat 触发背景见 docs/superpowers/specs/2026-08-21-gitnexus-track-hardening-design.md §1.3 断点 3。
"""
from supernova_core.code_index.parameter_models import SinkCategory, SlotContext


def _src_provider(src: str):
    src_bytes = src.encode("utf-8")
    def _provide(block):
        return src_bytes
    return _provide


def _detect(src: str, filename: str = "app.ts"):
    from supernova_core.code_index.sink_detector import detect_sinks
    from supernova_core.code_index.parsers.typescript_parser import TypeScriptParser
    import tempfile, pathlib
    parser = TypeScriptParser()
    with tempfile.TemporaryDirectory() as td:
        fpath = pathlib.Path(td) / filename
        fpath.write_text(src)
        blocks = parser.parse_file(fpath, pathlib.Path(td))
    return detect_sinks(blocks, parser, source_provider=_src_provider(src))


# ===== 修复点 C: ts-eval slot 纠正 =====

class TestTsEvalSlotFix:
    def test_ts_eval_slot_is_cmd_argument(self):
        """eval(污点) 命中 ts-eval 且 slot=cmd_argument(原 generic 路由不进 injection)。"""
        sites = _detect(
            "function f(req: any) {\n"
            "  const preTax = eval(req.body.preTax);\n"
            "}\n"
        )
        hit = [s for s in sites if s.rule_id == "ts-eval"]
        assert hit, "eval(req.body.preTax) 应命中 ts-eval"
        slots = hit[0].dangerous_slots
        assert slots, "ts-eval 命中须带 dangerous_slots"
        assert slots[0].slot == SlotContext.CMD_ARGUMENT, (
            "ts-eval slot 须为 cmd_argument(spec C;原 generic 不在 _INJECTION_SLOTS)"
        )

    def test_ts_eval_expression_carries_taint(self):
        """slot 表达式保留污点表达式(req.body.preTax),供 intra-first 回退匹配 SourcePoint。"""
        sites = _detect(
            "function f(req: any) {\n"
            "  const v = eval(req.body.afterTax);\n"
            "}\n"
        )
        hit = [s for s in sites if s.rule_id == "ts-eval"]
        assert hit and hit[0].dangerous_slots[0].expression == "req.body.afterTax"


# ===== 修复点 D: ts-res-render XSS 规则 =====

class TestTsResRenderRule:
    def test_res_render_with_locals_hits_xss(self):
        """res.render('tpl', {user}) 命中 ts-res-render,category=xss,污点槽 arg_index=1。"""
        sites = _detect(
            "function f(res: any, user: any) {\n"
            "  return res.render('profile', { user });\n"
            "}\n"
        )
        hit = [s for s in sites if s.rule_id == "ts-res-render"]
        assert hit, "res.render('profile', { user }) 应命中 ts-res-render"
        s = hit[0]
        assert s.category == SinkCategory.XSS
        assert s.sink_subtype == "xss_server_render"
        assert s.dangerous_slots, "ts-res-render 命中须带 dangerous_slots(locals 槽)"
        assert s.dangerous_slots[0].arg_index == 1, "污点在 arg1(locals),arg0 是模板名"
        assert s.needs_review is True, "autoescape 上下文未知,needs_review_default=True"

    def test_res_render_receiver_variants_hit(self):
        """response.render / ctx.render 同样命中(receiver 收窄集合内)。"""
        for recv in ("response", "ctx"):
            sites = _detect(
                f"function f({recv}: any) {{\n"
                f"  return {recv}.render('x', {{ data: 1 }});\n"
                f"}}\n"
            )
            assert any(s.rule_id == "ts-res-render" for s in sites), (
                f"{recv}.render 应命中 ts-res-render"
            )

    def test_bare_render_no_hit(self):
        """裸 render('tpl', data) 不命中 —— receiver 必配(ts-res-redirect 死规则教训)。"""
        sites = _detect(
            "function f(data: any) {\n"
            "  return render('x', data);\n"
            "}\n"
        )
        assert not any(s.rule_id == "ts-res-render" for s in sites)

    def test_foreign_receiver_render_no_hit(self):
        """foo.render(...) receiver 不在集合 → 不命中(防误报泛化)。"""
        sites = _detect(
            "function f(foo: any, data: any) {\n"
            "  return foo.render('x', data);\n"
            "}\n"
        )
        assert not any(s.rule_id == "ts-res-render" for s in sites)
