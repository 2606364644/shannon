from shannon_core.agents.tools_openai import build_tools


def test_build_tools_returns_nine():
    tools = build_tools()
    names = {t.name for t in tools}  # agents function_tool 暴露 .name
    assert names == {
        "bash", "read_file", "write_file", "edit_file",
        "grep", "glob", "web_fetch", "web_search", "task",
    }
