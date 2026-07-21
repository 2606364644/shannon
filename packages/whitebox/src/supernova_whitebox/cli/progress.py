import sys
import threading


class ProgressIndicator:
    """Terminal spinner animation for long-running agent execution."""

    def __init__(self, message: str = "Working...") -> None:
        self._message = message
        self._frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._index = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write("\r" + " " * (len(self._message) + 5) + "\r")
        sys.stdout.flush()
        self._running = False

    def finish(self, message: str = "Complete") -> None:
        self.stop()
        print(f"✓ {message}")

    def _spin(self) -> None:
        while not self._stop_event.is_set():
            frame = self._frames[self._index % len(self._frames)]
            sys.stdout.write(f"\r{frame} {self._message}")
            sys.stdout.flush()
            self._index += 1
            self._stop_event.wait(0.1)


class NullProgressIndicator:
    """No-op ProgressIndicator when spinner is disabled."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def finish(self, message: str = "Complete") -> None:
        pass


def create_progress_indicator(
    message: str, enabled: bool = True
) -> ProgressIndicator | NullProgressIndicator:
    """Factory: returns ProgressIndicator when enabled, NullProgressIndicator otherwise."""
    if enabled:
        return ProgressIndicator(message)
    return NullProgressIndicator()
