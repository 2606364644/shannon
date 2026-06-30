"""vuln 类选择纯函数：集中优先级链 CLI > env > YAML > 默认。

两个函数分层负责：
- resolve_vuln_classes: 合并「字符串来源」（CLI/env），返回 list 或 None。
- select_vuln_classes: 合并「list 来源」（override > YAML > 默认）。

env 必须在 CLI 层读取（workflow sandbox 不变量：workflow.run() 内禁 env 解析），
resolve 出的 override 以 list 形式进 PipelineInput.vuln_classes。
"""
from typing import Sequence

from shannon_core.models.config import ALL_VULN_CLASSES


class InvalidVulnClass(ValueError):
    """CLI/env 指定了不存在的 vuln 类。"""


def _parse_and_validate(raw: str, allowed: Sequence[str]) -> list[str]:
    """逗号分隔 → trim → 去空串 → 保序去重 → 校验每个 ∈ allowed。"""
    items: list[str] = []
    seen: set[str] = set()
    for token in raw.split(","):
        v = token.strip()
        if not v:
            continue
        if v not in allowed:
            raise InvalidVulnClass(
                f"未知的 vuln 类 {v!r}；合法值：{', '.join(allowed)}"
            )
        if v not in seen:
            seen.add(v)
            items.append(v)
    return items


def resolve_vuln_classes(
    cli_str: str | None,
    env_str: str | None,
    *,
    allowed: Sequence[str] = ALL_VULN_CLASSES,
) -> list[str] | None:
    """合并字符串来源：CLI > env。两者都空 → None（由调用方兜底 YAML/默认）。"""
    for raw in (cli_str, env_str):
        if raw and raw.strip():
            return _parse_and_validate(raw, allowed)
    return None


def select_vuln_classes(
    override: list[str] | None,
    yaml_vuln: list[str] | None,
    *,
    default: Sequence[str] = ALL_VULN_CLASSES,
) -> list[str]:
    """合并 list 来源：override（CLI/env 已解析）> YAML > 默认全跑。"""
    if override:
        return list(override)
    if yaml_vuln:
        return list(yaml_vuln)
    return list(default)
