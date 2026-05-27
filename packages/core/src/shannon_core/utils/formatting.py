from datetime import datetime, timezone

def format_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()

def truncate_text(text: str, max_length: int = 200) -> str:
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
