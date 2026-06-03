import json
import time
from typing import Any

import aiofiles

from shannon_core.models.metrics import SessionMetadata
from shannon_whitebox.audit.log_stream import LogStream
from shannon_whitebox.audit.utils import (
    format_timestamp,
    generate_log_path,
    generate_prompt_path,
)


class AgentLogger:
    """JSON Lines agent log with a text header."""

    def __init__(self, session_metadata: SessionMetadata, agent_name: str, attempt_number: int):
        self._meta = session_metadata
        self._agent_name = agent_name
        self._attempt = attempt_number
        self._stream: LogStream | None = None

    async def initialize(self) -> None:
        """Open the log file and write the text header + agent_start event."""
        timestamp_ms = int(time.time() * 1000)
        path = generate_log_path(self._meta, self._agent_name, timestamp_ms, self._attempt)
        self._stream = LogStream(path)
        await self._stream.open()

        header = (
            "========================================\n"
            f"Agent: {self._agent_name}\n"
            f"Attempt: {self._attempt}\n"
            f"Started: {format_timestamp()}\n"
            f"Session: {self._meta.id}\n"
            f"Web URL: {self._meta.web_url or 'N/A'}\n"
            "========================================\n\n"
        )
        await self._stream.write(header)
        await self.log_event("agent_start", {
            "agentName": self._agent_name,
            "attemptNumber": self._attempt,
        })

    async def log_event(self, event_type: str, event_data: Any) -> None:
        """Append a JSON Lines event to the agent log."""
        if self._stream is None:
            return
        event = {
            "type": event_type,
            "timestamp": format_timestamp(),
            "data": event_data,
        }
        await self._stream.write(json.dumps(event) + "\n")

    async def close(self) -> None:
        """Flush and close the underlying stream."""
        if self._stream is not None:
            await self._stream.close()
            self._stream = None

    @staticmethod
    async def save_prompt(session_metadata: SessionMetadata, agent_name: str, content: str) -> None:
        """Save a prompt snapshot as a Markdown file with YAML front-matter."""
        path = generate_prompt_path(session_metadata, agent_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "---\n"
            f"agent: {agent_name}\n"
            f"session: {session_metadata.id}\n"
            f"saved: {format_timestamp()}\n"
            "---\n\n"
        )
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(header + content)
