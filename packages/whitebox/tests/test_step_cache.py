"""step_cache 单测（spec 2026-08-27-web-resume-breakpoint §4.3）。

marker + 输入指纹（mtime_ns:size）+ outputs 清单 + 返回值快照；
任一不满足一律 fail-open 到重跑。
"""
import json
import os

import pytest

from supernova_core.utils.paths import INTERMEDIATE_SUBDIR
from supernova_whitebox.pipeline import step_cache


def _mk(tmp_path, name: str, content: dict | None = None) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(content if content is not None else {"v": name}),
                 encoding="utf-8")
    return str(p)


def _marker_path(deliverables, step: str):
    return deliverables / INTERMEDIATE_SUBDIR / ".step-cache" / f"{step}.json"


def _touch(path: str) -> None:
    """改 mtime_ns（不动内容/size），模拟上游重写。"""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))



def test_matching_fingerprint_skips_and_restores_ret(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp, out = _mk(tmp_path, "in.json"), _mk(tmp_path, "out.json")
    ret = {"failed_classes": [], "per_class": {"injection": 3}}

    step_cache.mark_done("gitnexus-chain-verdict", deliverables,
                         inputs=[inp], outputs=[out], ret=ret)

    skip, cached = step_cache.should_skip(
        "gitnexus-chain-verdict", deliverables, inputs=[inp])
    assert skip is True
    assert cached == ret



def test_input_mtime_change_forces_rerun(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp, out = _mk(tmp_path, "in.json"), _mk(tmp_path, "out.json")
    step_cache.mark_done("s", deliverables, inputs=[inp], outputs=[out], ret={})

    _touch(inp)

    skip, cached = step_cache.should_skip("s", deliverables, inputs=[inp])
    assert skip is False
    assert cached is None



def test_missing_input_forces_rerun(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp, out = _mk(tmp_path, "in.json"), _mk(tmp_path, "out.json")
    step_cache.mark_done("s", deliverables, inputs=[inp], outputs=[out], ret={})

    os.remove(inp)

    skip, cached = step_cache.should_skip("s", deliverables, inputs=[inp])
    assert skip is False
    assert cached is None



def test_missing_output_forces_rerun(tmp_path):
    """产物清单任一缺失（人工误删/磁盘损坏）→ 重跑重建。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp = _mk(tmp_path, "in.json")
    out1, out2 = _mk(tmp_path, "o1.json"), _mk(tmp_path, "o2.json")
    step_cache.mark_done("s", deliverables, inputs=[inp],
                         outputs=[out1, out2], ret={})

    os.remove(out2)

    skip, cached = step_cache.should_skip("s", deliverables, inputs=[inp])
    assert skip is False
    assert cached is None



def test_corrupt_marker_forces_rerun(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp, out = _mk(tmp_path, "in.json"), _mk(tmp_path, "out.json")
    step_cache.mark_done("s", deliverables, inputs=[inp], outputs=[out], ret={})

    _marker_path(deliverables, "s").write_text("not json", encoding="utf-8")

    skip, cached = step_cache.should_skip("s", deliverables, inputs=[inp])
    assert skip is False
    assert cached is None



def test_no_marker_forces_rerun(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp = _mk(tmp_path, "in.json")

    skip, cached = step_cache.should_skip("s", deliverables, inputs=[inp])
    assert skip is False
    assert cached is None



def test_ret_none_roundtrip(tmp_path):
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp, out = _mk(tmp_path, "in.json"), _mk(tmp_path, "out.json")
    step_cache.mark_done("s", deliverables, inputs=[inp], outputs=[out],
                         ret=None)

    skip, cached = step_cache.should_skip("s", deliverables, inputs=[inp])
    assert skip is True
    assert cached is None



def test_missing_input_at_mark_time_skips_while_still_missing(tmp_path):
    """mark 时输入缺失（记录 None）→ 仍缺失则跳过（一致的缺失语义）；
    之后出现（上游补产）→ None≠指纹 → 重跑。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp, out = _mk(tmp_path, "in.json"), _mk(tmp_path, "out.json")
    absent = str(tmp_path / "absent.json")
    step_cache.mark_done("s", deliverables, inputs=[inp, absent],
                         outputs=[out], ret={})

    skip, _ = step_cache.should_skip("s", deliverables, inputs=[inp, absent])
    assert skip is True

    (tmp_path / "absent.json").write_text('{"new": 1}', encoding="utf-8")
    skip2, _ = step_cache.should_skip("s", deliverables, inputs=[inp, absent])
    assert skip2 is False


def test_salt_mismatch_forces_rerun(tmp_path):
    """salt（env 开关等非文件依赖）不匹配 → fail-open 重跑。

    场景：SUPERNOVA_GITNEXUS_LLM_ENABLED 在两次 resume 之间翻转——env off 跑出
    的 unadjudicated 结果不得在 env on 的续跑里被缓存跳过复用。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    inp, out = _mk(tmp_path, "in.json"), _mk(tmp_path, "out.json")
    step_cache.mark_done("s", deliverables, inputs=[inp], outputs=[out],
                         ret={}, salt="gn-llm=False")

    skip, _ = step_cache.should_skip("s", deliverables, inputs=[inp],
                                     salt="gn-llm=True")
    assert skip is False
    skip2, cached2 = step_cache.should_skip("s", deliverables, inputs=[inp],
                                            salt="gn-llm=False")
    assert skip2 is True
    assert cached2 == {}


def test_input_set_change_forces_rerun(tmp_path):
    """调用侧输入清单与 marker 记录不一致（增/减）→ fail-open 重跑。"""
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    in1, in2 = _mk(tmp_path, "in1.json"), _mk(tmp_path, "in2.json")
    out = _mk(tmp_path, "out.json")
    step_cache.mark_done("s", deliverables, inputs=[in1], outputs=[out], ret={})

    # 新版本接线多了一个输入：marker 未覆盖 → 不可跳
    skip, _ = step_cache.should_skip("s", deliverables, inputs=[in1, in2])
    assert skip is False
    # 反向：marker 记了两个、调用只传一个 → 同样不可跳
    step_cache.mark_done("s2", deliverables, inputs=[in1, in2],
                         outputs=[out], ret={})
    skip2, _ = step_cache.should_skip("s2", deliverables, inputs=[in1])
    assert skip2 is False


def test_preview_steps_states(tmp_path):
    """resume-preview（spec §4.5）：已知 2 步的缓存状态简表——
    done（指纹此刻仍匹配）/ stale（输入已变或产物缺失）/ missing（无 marker）。"""
    from supernova_whitebox.pipeline.step_cache import preview_steps
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    d = tmp_path / "d2"
    d.mkdir()
    in1, out1 = _mk(d, "in1.json"), _mk(d, "out1.json")
    in2, out2 = _mk(d, "in2.json"), _mk(d, "out2.json")
    from supernova_whitebox.pipeline.step_cache import (
        STEP_AUTHZ_GITNEXUS_JUDGE, STEP_GITNEXUS_CHAIN_VERDICT)
    step_cache.mark_done(STEP_AUTHZ_GITNEXUS_JUDGE, deliverables,
                         inputs=[in1], outputs=[out1], ret={})
    step_cache.mark_done(STEP_GITNEXUS_CHAIN_VERDICT, deliverables,
                         inputs=[in2], outputs=[out2], ret={})
    _touch(in2)  # chain-verdict 输入已变 → stale

    rows = {r["step"]: r for r in preview_steps(deliverables)}

    assert rows[STEP_AUTHZ_GITNEXUS_JUDGE]["state"] == "done"
    assert rows[STEP_AUTHZ_GITNEXUS_JUDGE]["ts"] is not None
    assert rows[STEP_GITNEXUS_CHAIN_VERDICT]["state"] == "stale"
    assert "输入" in rows[STEP_GITNEXUS_CHAIN_VERDICT]["reason"]
