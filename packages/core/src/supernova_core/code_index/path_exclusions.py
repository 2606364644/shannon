"""确定性层全局路径排除 —— 测试/构建/依赖目录 + 测试文件名（§4.6）。

四个文件发现入口共用（此前各自维护跳过集合且已漂移）：
  - parser.discover_source_files     源码清单（sink/source/候选检测的根）
  - entry_points._detect_typescript  Express 路由补充扫描
  - schema_entry_parser              OpenAPI/Swagger 扫描
  - file_discovery                   模板/config/schema 安全文件清单

为什么排除测试：测试代码按设计就在调危险 API（exec / db.query / ...），
留在确定性层清单里只会产出大量不构成真实攻击面的 SinkCallSite / SourcePoint
假链（降噪）。只收窄确定性层的文件集合；LLM 轨 agent 自己 grep 全仓，不受影响。

隐藏目录（.git / .github 等）由各调用点的 part.startswith(".") 既有逻辑处理，
这里只管显式名单。
"""

import re
from typing import Iterable

# 目录名：出现在相对路径任何一级即跳过。
# 依赖/缓存/虚拟环境 = 原有四处的并集；构建产物补 target（Maven/Rust）；
# 测试目录按各语言惯例收录（__tests__ JS、tests/ Py/Java、src/test Java 由
# 「test」段命中、fixtures / spec / e2e / coverage 同类）。
SKIP_DIRS: frozenset[str] = frozenset({
    # VCS / 缓存 / 依赖
    ".git", ".hg", ".svn", "node_modules", "vendor", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", ".gitnexus",
    # 虚拟环境 / eggs
    ".venv", "venv", "env", ".eggs", "eggs",
    # 构建产物
    "dist", "build", ".next", "target", "coverage",
    # 测试 / fixture
    "test", "tests", "__tests__", "__test__",
    "fixtures", "fixture", "__mocks__", "__snapshots__",
    "spec", "specs", "e2e",
})

# 测试目录之外的测试文件（按语言惯例）：
#   jest/mocha   foo.test.ts / foo.spec.js     → .test. / .spec.
#   pytest       test_foo.py / foo_test.py     → ^test_ / _test.py$
#   Go           foo_test.go                   → _test.go$
# Java 主流形态是目录（src/test/java），目录级已覆盖，不做文件名匹配
# （避免误伤 src/main 下以 Test 结尾的业务类名）。
_TEST_FILE_RE = re.compile(r"(?:\.test\.|\.spec\.|^test_|_test\.(?:py|go)$)", re.IGNORECASE)


def is_excluded_dir(part: str) -> bool:
    """目录名是否在排除名单（供 os.walk 剪枝 / 相对路径分段检查）。"""
    return part in SKIP_DIRS


def is_test_file_name(name: str) -> bool:
    """文件名是否命中测试文件模式（不区分大小写）。"""
    return bool(_TEST_FILE_RE.search(name))


def should_skip_parts(parts: Iterable[str]) -> bool:
    """相对路径分段（含文件名）是否应跳过：任一目录段命中名单，或文件名命中测试模式。"""
    parts = tuple(parts)
    if not parts:
        return False
    return any(is_excluded_dir(p) for p in parts[:-1]) or is_test_file_name(parts[-1])
