from supernova_core.code_index.template_escape_detector import (
    detect_template_escape,
    detect_template_escapes,
)


def test_detects_ejs_unescaped(tmp_path):
    f = tmp_path / "a.ejs"
    f.write_text("<%= safe %>\n<%- unsafe %>\n")

    findings = detect_template_escape(f)

    unescaped = [x for x in findings if x.escaping == "unescaped"]
    assert len(unescaped) == 1
    assert unescaped[0].line == 2
    assert "ejs" in unescaped[0].engine


def test_detects_jinja2_safe(tmp_path):
    f = tmp_path / "b.jinja2"
    f.write_text("{{ x }}\n{{ y | safe }}\n")

    findings = detect_template_escape(f)

    unescaped = [x for x in findings if x.escaping == "unescaped"]
    assert any("jinja" in x.engine for x in unescaped)


def test_detects_mustache_triple(tmp_path):
    f = tmp_path / "c.hbs"
    f.write_text("{{ escaped }}\n{{{ unescaped }}}\n")

    findings = detect_template_escape(f)

    unescaped = [x for x in findings if x.escaping == "unescaped"]
    assert any("mustache" in x.engine or "triple" in x.engine for x in unescaped)


def test_multiple_files(tmp_path):
    (tmp_path / "a.ejs").write_text("<%- x %>")
    (tmp_path / "b.jinja2").write_text("{{ x | safe }}")

    findings = detect_template_escapes([tmp_path / "a.ejs", tmp_path / "b.jinja2"])

    assert len(findings) == 2


def test_unreadable_file_skipped(tmp_path):
    findings = detect_template_escape(tmp_path / "nonexistent.ejs")

    assert findings == []
