from shannon_core.code_index.sanitizer_library import (
    SanitizerLibrary,
    annotate_sanitizers,
)
from shannon_core.code_index.parameter_models import PropagationStep


def _step(transformation, code_location="app.py:10"):
    return PropagationStep(
        step_id="s1", from_func_id="f", from_param="q",
        to_func_id="f", to_param="x",
        transformation=transformation, code_location=code_location,
    )


def test_library_known_sql_bind_pattern():
    lib = SanitizerLibrary()
    # execute with placeholder arg pattern — best-effort detection
    hit = lib.match(language="python", callee="execute",
                    receiver_text="cursor", arg_expr="%s")
    assert hit is not None
    assert hit.defense_type == "sql_bind"
    assert hit.applies_to == "sql_value"


def test_library_known_html_escape():
    lib = SanitizerLibrary()
    hit = lib.match(language="python", callee="escape", receiver_text="html", arg_expr="q")
    assert hit is not None
    assert hit.defense_type == "html_entity_encode"
    assert hit.applies_to == "html_body"


def test_library_known_dompurify():
    lib = SanitizerLibrary()
    hit = lib.match(language="typescript", callee="sanitize",
                    receiver_text="DOMPurify", arg_expr="x")
    assert hit is not None
    assert hit.defense_type == "dom_purify"
    assert hit.applies_to == "html_body"


def test_library_unknown_returns_none():
    lib = SanitizerLibrary()
    assert lib.match(language="python", callee="unknown_fn",
                     receiver_text=None, arg_expr="q") is None


def test_annotate_sanitizers_finds_defense_on_step_transformation():
    """propagation_steps 里的 transformation 字段出现 sanitizer -> 标注。"""
    # transformation 字段约定: "sanitize_hint:<name>" (见 parameter_models.py:47)
    steps = [
        _step("concat"),
        _step("sanitize_hint:html.escape"),
        _step("format"),
    ]
    annotations = annotate_sanitizers(steps, language="python")
    # 至少识别到 html.escape（出现于 transformation 文本）
    defense_types = {a.defense_type for a in annotations}
    assert "html_entity_encode" in defense_types


def test_annotate_sanitizers_empty_when_no_defense():
    steps = [_step("concat"), _step("format")]
    assert annotate_sanitizers(steps, language="python") == []


def test_subprocess_shell_false_list_args_annotated_as_array():
    lib = SanitizerLibrary()
    hit = lib.match(
        language="python", callee="run", receiver_text="subprocess",
        arg_expr='["ls", "-la"], shell=False',
    )
    assert hit is not None
    assert hit.defense_type == "subprocess_array"
    assert hit.applies_to == "cmd_argument"


def test_subprocess_shell_true_no_array_defense():
    lib = SanitizerLibrary()
    hit = lib.match(
        language="python", callee="run", receiver_text="subprocess",
        arg_expr='"ls " + x, shell=True',
    )
    # shell=True 不标 array defense（保持判 vulnerable）
    assert hit is None or hit.defense_type != "subprocess_array"
