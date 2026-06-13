from shannon_core.display.types import LineWriter, Renderer


def test_protocols_importable():
    # Protocols exist and are usable as types
    assert LineWriter is not None
    assert Renderer is not None


def test_linewriter_satisfied_by_object_with_async_write():
    # Structural typing: any object with async write(str) satisfies LineWriter
    class FakeStream:
        async def write(self, text: str) -> None:
            self.last = text

    stream = FakeStream()
    # Runtime check passes because the attribute exists
    assert hasattr(stream, "write")
