"""mr_scan.diff_manifest — git diff -U3 解析（spec 2026-09-03 §4.1）。

fixture 均为真实 `git diff -U3` 输出格式（内嵌保确定性；端到端格式核对见集成测试）。
"""

from supernova_core.mr_scan.diff_manifest import parse_unified_diff

_BASIC_DIFF = """\
diff --git a/app/routes.py b/app/routes.py
index 1111111..2222222 100644
--- a/app/routes.py
+++ b/app/routes.py
@@ -10,3 +10,4 @@ def old_handler(req):
     ctx = "context"
-    query = req.args.get("q")
+    query = sanitize(req.args.get("q"))
+    extra = req.args.get("e")
     return render(query)
"""


def test_parse_basic_hunk_records_file_and_line_numbers():
    manifest = parse_unified_diff(_BASIC_DIFF, base_commit="b1", head_commit="h1")

    assert manifest.base_commit == "b1"
    assert manifest.head_commit == "h1"
    assert len(manifest.hunks) == 1
    hunk = manifest.hunks[0]
    assert hunk.file_path == "app/routes.py"
    assert (hunk.old_start, hunk.old_lines) == (10, 3)
    assert (hunk.new_start, hunk.new_lines) == (10, 4)
    # removed 行带 base 侧行号
    assert [(l.base_line_no, l.text) for l in hunk.removed] == [
        (11, '    query = req.args.get("q")'),
    ]
    # added 行带 head 侧行号（与 head 索引 SinkCallSite.line 同坐标系）
    assert [(l.head_line_no, l.text) for l in hunk.added] == [
        (11, '    query = sanitize(req.args.get("q"))'),
        (12, '    extra = req.args.get("e")'),
    ]


_NEW_FILE_DIFF = """\
diff --git a/app/new_handler.py b/app/new_handler.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/app/new_handler.py
@@ -0,0 +1,2 @@
+def new_handler(req):
+    return render(req.args.get("x"))
"""


def test_parse_new_file_marks_flag_and_lines_start_at_one():
    manifest = parse_unified_diff(_NEW_FILE_DIFF, base_commit="b1", head_commit="h1")

    hunk = manifest.hunks[0]
    assert hunk.is_new_file is True
    assert hunk.file_path == "app/new_handler.py"
    # 新文件 added 行号从 1 起（head 侧）
    assert [l.head_line_no for l in hunk.added] == [1, 2]
    assert hunk.removed == []


_DELETED_FILE_DIFF = """\
diff --git a/app/legacy.py b/app/legacy.py
deleted file mode 100644
index 4444444..0000000
--- a/app/legacy.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def legacy_handler(req):
-    return eval(req.args.get("x"))
"""


def test_parse_deleted_file_marks_flag_and_removed_lines_start_at_one():
    manifest = parse_unified_diff(_DELETED_FILE_DIFF, base_commit="b1", head_commit="h1")

    hunk = manifest.hunks[0]
    assert hunk.is_deleted_file is True
    assert [l.base_line_no for l in hunk.removed] == [1, 2]
    assert hunk.added == []


_PURE_RENAME_DIFF = """\
diff --git a/services/auth_old.py b/services/auth_new.py
similarity index 96%
rename from services/auth_old.py
rename to services/auth_new.py
"""


def test_parse_pure_rename_yields_hunk_with_rename_from():
    manifest = parse_unified_diff(_PURE_RENAME_DIFF, base_commit="b1", head_commit="h1")

    assert len(manifest.hunks) == 1
    hunk = manifest.hunks[0]
    assert hunk.file_path == "services/auth_new.py"
    assert hunk.rename_from == "services/auth_old.py"
    assert hunk.added == [] and hunk.removed == []


def test_resolve_head_path_normalizes_base_side_path():
    from supernova_core.mr_scan.diff_manifest import parse_unified_diff as parse

    manifest = parse(_PURE_RENAME_DIFF, base_commit="b1", head_commit="h1")
    # 旧路径（base 侧，LLM 可能从 diff 文本报出）→ 归一到新侧
    assert manifest.resolve_head_path("services/auth_old.py") == "services/auth_new.py"
    # 新侧路径原样返回；无关路径原样返回
    assert manifest.resolve_head_path("services/auth_new.py") == "services/auth_new.py"
    assert manifest.resolve_head_path("app/routes.py") == "app/routes.py"


def test_added_line_set_returns_head_side_line_numbers_per_file():
    manifest = parse_unified_diff(_BASIC_DIFF, base_commit="b1", head_commit="h1")

    assert manifest.added_line_set("app/routes.py") == {11, 12}
    assert manifest.added_line_set("not/touched.py") == set()
