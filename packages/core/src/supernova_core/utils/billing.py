import re

_SPENDING_CAP_PATTERNS: list[re.Pattern] = [
    # Text patterns (from agent output)
    re.compile(r"spending\s+cap", re.IGNORECASE),
    re.compile(r"spending\s+limit", re.IGNORECASE),
    re.compile(r"budget\s+exceeded", re.IGNORECASE),
    re.compile(r"credit\s+limit", re.IGNORECASE),
    re.compile(r"usage\s+limit", re.IGNORECASE),
    re.compile(r"rate\s+limit", re.IGNORECASE),
    re.compile(r"cap\s+reached", re.IGNORECASE),
    re.compile(r"monthly\s+limit", re.IGNORECASE),
    # API error patterns (from provider responses)
    re.compile(r"billing[\s_]+error", re.IGNORECASE),
    re.compile(r"credit\s+balance\s+is\s+too\s+low", re.IGNORECASE),
    re.compile(r"insufficient\s+credits", re.IGNORECASE),
    re.compile(r"usage\s+is\s+blocked\s+due\s+to\s+insufficient\s+credits", re.IGNORECASE),
    re.compile(r"please\s+visit\s+plans\s+&\s+billing", re.IGNORECASE),
    re.compile(r"please\s+visit\s+plans\s+and\s+billing", re.IGNORECASE),
    re.compile(r"usage\s+limit\s+reached", re.IGNORECASE),
    re.compile(r"quota\s+exceeded", re.IGNORECASE),
    re.compile(r"daily\s+rate\s+limit", re.IGNORECASE),
    re.compile(r"limit\s+will\s+reset", re.IGNORECASE),
    re.compile(r"billing\s+limit\s+reached", re.IGNORECASE),
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
