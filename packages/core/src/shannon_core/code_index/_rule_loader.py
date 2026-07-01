"""code_index 规则 YAML 加载器(共享)。

sink / source / sink_candidate 规则外部化到 ``data/*.yml``。本模块只负责读取
YAML → dict;各 detector 用自己的 ``_build_*`` 把 dict 构造成 dataclass —— 这样
loader 不依赖 SinkRule/SourceRule 的定义处,避免循环 import。

容错策略(fail-fast):YAML 解析错误直接抛 ``yaml.YAMLError``,**不回退硬编码**。
规则是可信内部数据,旧硬编码将被删除、无回退目标;掩盖错误违背外部化初衷,加载
失败就该让 import 失败、CI 红。枚举值未知也由各 detector 的 ``_build_*`` 直接抛
ValueError(fail-fast),不像 ``sink_discovery_llm._to_category`` 那样回落 —— 后者
是 LLM 不可信输出容错,本模块面对的是可信内部 YAML。

复用先例:``config/parser.py`` 的 ``yaml.safe_load``。
"""
from pathlib import Path

import yaml

#: 规则 YAML 所在目录(包内 data/,随 wheel 一起打包,见 pyproject force-include)。
DATA_DIR = Path(__file__).parent / "data"


def load_yaml(path: Path) -> dict:
    """读取 YAML 文件 → dict(UTF-8)。失败抛 ``yaml.YAMLError``(fail-fast)。"""
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)
