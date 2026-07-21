"""回归测试:log_phase_start_activity 的 args 数量必须 == 参数数量(3)。

temporalio worker 反序列化 activity 入参时,若 args 数量 != 函数参数数量,会把整个
arg_types 置 None(见 temporalio worker/_activity.py 中 `len(arg_types) != len(start.input)
→ arg_types = None`)。这导致 JSONPlainPayloadConverter 反序列化时无 type_hint,
ActivityInput payload 被还原成 dict(而非 ActivityInput 实例),activity 内
``input.phase`` 抛 AttributeError。

历史 bug:vuln 阶段只传 2 个 args(input + steps),漏第 3 个 intents 参数,
导致 vulnerability-analysis phase 的 log_phase_start_activity 无限重试崩溃,
workflow 卡在 running。修复:workflows.py vuln 调用补第 3 个 intents 参数。
"""
import asyncio

from temporalio import activity
from temporalio.converter import DataConverter

from supernova_whitebox.pipeline.activities import log_phase_start_activity
from supernova_whitebox.pipeline.shared import ActivityInput


def _decode_after_worker_rule(args: list) -> list:
    """模拟 temporalio worker 的反序列化规则:
    len(arg_types) != len(payloads) → arg_types = None。"""
    arg_types = list(activity._Definition.from_callable(log_phase_start_activity).arg_types)
    hints = arg_types if len(arg_types) == len(args) else None

    async def _run() -> list:
        payloads = await DataConverter.default.encode(args)
        return await DataConverter.default.decode(payloads, hints)

    return asyncio.run(_run())


def test_three_args_decode_input_as_activity_input():
    """3 个 args(== 参数数 3)→ input 是 ActivityInput。所有 log_phase_start 调用必须如此。"""
    base = ActivityInput(repo_path="/tmp/x")
    args = [
        ActivityInput(**{**base.__dict__, "phase": "vulnerability-analysis"}),
        ["injection-vuln"],
        ["分析 injection 漏洞"],
    ]
    decoded = _decode_after_worker_rule(args)
    assert isinstance(decoded[0], ActivityInput)
    assert decoded[0].phase == "vulnerability-analysis"


def test_two_args_decode_input_as_dict_documents_bug():
    """2 个 args(!= 参数数 3)→ worker 置 arg_types=None → input 被还原成 dict。
    这就是历史 vuln 崩溃的根因(input.phase AttributeError)。本测试文档化该行为,
    防止未来有人把 log_phase_start_activity 调用改回 2 args。"""
    base = ActivityInput(repo_path="/tmp/x")
    args = [
        ActivityInput(**{**base.__dict__, "phase": "vulnerability-analysis"}),
        ["injection-vuln"],
    ]
    decoded = _decode_after_worker_rule(args)
    assert isinstance(decoded[0], dict)
    assert decoded[0]["phase"] == "vulnerability-analysis"
