import re

_SPENDING_CAP_PATTERNS: list[re.Pattern] = [
    re.compile(r"spending\s+cap", re.IGNORECASE),
    re.compile(r"spending\s+limit", re.IGNORECASE),
    re.compile(r"budget\s+exceeded", re.IGNORECASE),
    re.compile(r"credit\s+limit", re.IGNORECASE),
    re.compile(r"usage\s+limit", re.IGNORECASE),
    re.compile(r"rate\s+limit", re.IGNORECASE),
]

def is_spending_cap_behavior(turns: int, cost: float, text: str) -> bool:
    if turns > 2:
        return False
    if cost > 0:
        return False
    for pattern in _SPENDING_CAP_PATTERNS:
        if pattern.search(text):
            return True
    return False
