"""path_exclusions 共享排除模块（§4.6）—— 目录级与文件名级过滤口径。"""

from supernova_core.code_index.path_exclusions import (
    SKIP_DIRS,
    is_excluded_dir,
    is_test_file_name,
    should_skip_parts,
)


class TestIsExcludedDir:
    def test_test_dirs_excluded(self):
        for d in ("test", "tests", "__tests__", "fixtures", "spec", "e2e"):
            assert is_excluded_dir(d), d

    def test_build_and_dep_dirs_excluded(self):
        for d in ("dist", "build", ".next", "target", "coverage", "node_modules", "vendor", ".gitnexus"):
            assert is_excluded_dir(d), d

    def test_source_dirs_not_excluded(self):
        for d in ("src", "app", "internal", "handlers", "api", "main"):
            assert not is_excluded_dir(d), d


class TestIsTestFileName:
    def test_jest_mocha_patterns(self):
        for name in ("foo.test.ts", "foo.spec.js", "app.test.tsx", "api.spec.jsx"):
            assert is_test_file_name(name), name

    def test_pytest_patterns(self):
        for name in ("test_foo.py", "foo_test.py", "test_utils.py"):
            assert is_test_file_name(name), name

    def test_go_pattern(self):
        assert is_test_file_name("handler_test.go")
        assert is_test_file_name("main_test.go")

    def test_normal_files_not_matched(self):
        for name in ("app.ts", "test.ts", "tester.py", "latest.go", "contest.ts", "attest.py"):
            assert not is_test_file_name(name), name

    def test_case_insensitive(self):
        assert is_test_file_name("Foo.Test.ts")


class TestShouldSkipParts:
    def test_dir_any_level_skips(self):
        assert should_skip_parts(("src", "__tests__", "foo.test.ts"))
        assert should_skip_parts(("tests", "conftest.py"))
        assert should_skip_parts(("packages", "app", "fixtures", "seed.py"))

    def test_test_filename_outside_test_dir_skips(self):
        assert should_skip_parts(("src", "foo.test.ts"))
        assert should_skip_parts(("handlers", "user_test.go"))

    def test_normal_path_kept(self):
        assert not should_skip_parts(("src", "app.ts"))
        assert not should_skip_parts(("handlers", "user.go"))
        assert not should_skip_parts(("main.py",))

    def test_empty_parts_kept(self):
        assert not should_skip_parts(())

    def test_skip_dirs_is_frozenset(self):
        assert isinstance(SKIP_DIRS, frozenset)
