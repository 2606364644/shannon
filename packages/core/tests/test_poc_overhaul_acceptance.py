# packages/core/tests/test_poc_overhaul_acceptance.py
"""PoC 治理（spec 2026-08-19 §7）fixture 重放量化验收。

fixture 取自 5 扫描真实产物（NodeGoat 08-12 + hk 08-05），裁剪入
tests/fixtures/poc_overhaul/。gapped 条目由 mock gap-fill 补回（对齐真机
LLM 读码行为），断言端到端 md 的量化指标。
"""
import json
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from supernova_core.models.queue_schemas import VulnerabilityQueue
from supernova_core.services.poc_generator import PoCGenerator

FIXTURES = Path(__file__).parent / "fixtures" / "poc_overhaul"

# NodeGoat gapped 条目的 gap-fill 补回表（对齐真机 LLM 读码会给出的路由；
# INJ-05 session.js→/login、XSS-01 注册表单→/signup、其余 profile/research 类）。
_NODEGOAT_GAP = {
    "INJ-VULN-05": ("POST", "/login", '{"userName":{"$gt":""},"password":"Admin_123"}'),
    "XSS-VULN-01": ("POST", "/signup", "firstName=<img src=x onerror=alert(1)>"),
    "XSS-VULN-05": ("POST", "/profile", "lastName=<script>alert(1)</script>"),
    "XSS-VULN-06": ("POST", "/profile", "address=<script>alert(1)</script>"),
    "XSS-VULN-09": ("POST", "/memos", "memo=<img src=x onerror=alert(1)>"),
    "XSS-VULN-10": ("POST", "/benefits", "proName=<script>alert(1)</script>"),
    "XSS-VULN-11": ("POST", "/signup", "userName=<script>alert(1)</script>"),
    "XSS-VULN-13": ("POST", "/profile", "firstName=\" onmouseover=\"alert(1)"),
    "SSRF-VULN-01": ("POST", "/research", "query=http://127.0.0.1:8080/"),
    "SSRF-VULN-02": ("GET", "/tutorial", "next=http://169.254.169.254/"),
}

_DETAIL_HEADING_RE = re.compile(r"^### [✓●⚠] .+? @ (\w+) (\S+)$", re.MULTILINE)


def _load_queues(name):
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {cls: [SimpleNamespace(**v) for v in items] for cls, items in data.items()}


def _write_queue(d: Path, vulns_by_cls):
    d.mkdir(parents=True, exist_ok=True)
    for cls, vulns in vulns_by_cls.items():
        queue = {"vulnerabilities": [v.__dict__ for v in vulns]}
        (d / f"{cls}_exploitation_queue.json").write_text(
            json.dumps(queue, ensure_ascii=False), encoding="utf-8")


async def _replay_nodegoat(tmp_path, monkeypatch, *, with_gapfill=True) -> str:
    """NodeGoat fixture 重放：inj/xss/ssrf 队列 + entry_points join + mock gap-fill。"""
    d = tmp_path / "deliverables" / "whitebox"
    _write_queue(d, _load_queues("nodegoat_queues.json"))
    (d / "entry_points.json").write_text(
        (FIXTURES / "nodegoat_entry_points.json").read_text(encoding="utf-8"),
        encoding="utf-8")

    import supernova_core.services.poc_generator as mod

    async def fake_run(prompt, **kw):
        items = []
        for vid, (m, p, w) in _NODEGOAT_GAP.items():
            if vid in prompt:
                items.append({"ID": vid, "http_method": m, "route_path": p,
                              "witness_payload": w, "param_location": "body" if m == "POST" else "query"})
        return SimpleNamespace(success=True, structured_output={"items": items}, error=None)

    if with_gapfill:
        monkeypatch.setattr(mod, "run_claude_prompt", fake_run)
    else:
        async def boom(prompt, **kw):
            raise RuntimeError("llm down")
        monkeypatch.setattr(mod, "run_claude_prompt", boom)

    out = await PoCGenerator.generate(
        d, ["injection", "xss", "ssrf"], "https://nodegoat.example.com",
        "whitebox", repo_path="/tmp/x")
    return out.read_text(encoding="utf-8")


class TestNodeGoatQuantitativeAcceptance:
    async def test_route_resolution_at_least_90pct(self, tmp_path, monkeypatch):
        """路由解析率：非 `/` 塌缩 ≥90%（spec §7；确定性 join 11/19 + gap-fill 补回）。"""
        md = await _replay_nodegoat(tmp_path, monkeypatch)
        headings = _DETAIL_HEADING_RE.findall(md)
        assert headings, "no detail sections rendered"
        collapsed = [h for h in headings if h[1] in ("/",)]
        resolution = 1 - len(collapsed) / len(headings)
        assert resolution >= 0.90, f"route resolution {resolution:.0%}: collapsed={collapsed}"

    async def test_placeholder_leak_zero(self, tmp_path, monkeypatch):
        """占位符泄漏 = 0（渲染文本不含 witness_payload/${{）。"""
        md = await _replay_nodegoat(tmp_path, monkeypatch)
        assert "witness_payload" not in md
        assert "${" not in md
        assert "{{" not in md

    async def test_no_byte_identical_duplicate_curl_blocks(self, tmp_path, monkeypatch):
        """逐字节重复 curl 块 = 0（G8 去重合并后）。"""
        md = await _replay_nodegoat(tmp_path, monkeypatch)
        blocks = re.findall(r"```bash\n(.*?)```", md, re.DOTALL)
        assert blocks, "no curl blocks"
        assert len(blocks) == len(set(blocks)), f"{len(blocks) - len(set(blocks))} duplicate curl blocks"

    async def test_needs_review_not_rendered_confirmed(self, tmp_path, monkeypatch):
        """NodeGoat 全队列 needs_review/low → SUSPECTED 档，不出现「已确认」。"""
        md = await _replay_nodegoat(tmp_path, monkeypatch)
        assert "✓ 已确认" not in md
        assert "已确认（静态判定）" not in md
        assert "疑似待验证" in md

    async def test_burp_request_line_legal(self, tmp_path, monkeypatch):
        """Burp 请求行无空格（method/path/query 边界外）无非 ASCII。"""
        md = await _replay_nodegoat(tmp_path, monkeypatch)
        for block in re.findall(r"```http\n(.*?)```", md, re.DOTALL):
            line = block.splitlines()[0]
            method, _, rest = line.partition(" ")
            target, _, proto = rest.rpartition(" ")
            assert method in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"), line
            assert " " not in target, line
            assert all(ord(c) < 128 for c in line), line

    async def test_curl_lines_shlex_parseable(self, tmp_path, monkeypatch):
        """curl 命令行 POSIX 引号合法（shlex 可完整解析回原 payload）。"""
        md = await _replay_nodegoat(tmp_path, monkeypatch)
        blocks = re.findall(r"```bash\n(.*?)```", md, re.DOTALL)
        assert blocks
        for b in blocks:
            cmd = b.replace("\\\n", " ")
            parts = shlex.split(cmd)
            assert parts[0] == "curl"
            assert len(parts) >= 3  # curl -i -X METHOD 'url'

    async def test_llm_unavailable_no_worse_than_skeleton(self, tmp_path, monkeypatch):
        """LLM 完全不可用：确定性 join 命中的 11 条仍产出真实路由（0ms 层的价值），
        gapped 条目降级骨架但含明确标注，不崩、不空白。"""
        md = await _replay_nodegoat(tmp_path, monkeypatch, with_gapfill=False)
        assert "POST /contributions" in md  # RouteIndex join 产物
        assert "GET /allocations/:userId" in md
        assert "需手工补全" in md  # gapped 降级标注


class TestHkRequestLineWitnessAcceptance:
    async def test_request_line_witness_yields_real_route_and_params(self, tmp_path):
        """hk 请求行 witness（曾整体塞 id 参数）→ method/path/query 结构化落位。"""
        d = tmp_path / "deliverables" / "whitebox"
        vulns = _load_queues("hk_injection_queue.json")
        _write_queue(d, vulns)
        out = await PoCGenerator.generate(
            d, ["injection"], "https://hk.example.com", "whitebox")
        md = out.read_text(encoding="utf-8")
        assert "GET /api/v2/download-cer" in md
        assert "uid=999999" in md          # 参数名与值来自 witness 结构
        assert "spaceId=10249" in md
        assert "id=GET%20%2Fapi" not in md  # 不再把整串塞进 id 参数

    async def test_path_traversal_witness_parsed(self, tmp_path):
        d = tmp_path / "deliverables" / "whitebox"
        _write_queue(d, _load_queues("hk_injection_queue.json"))
        out = await PoCGenerator.generate(
            d, ["injection"], "https://hk.example.com", "whitebox")
        md = out.read_text(encoding="utf-8")
        assert "GET /api/download-img" in md
        assert "filename=" in md
