"""Task 5: 双轨 attack chain activity + workflow 编排 烟雾测试。

验证：
1. 新增的两个 activity 存在（run_attack_chain_llm_agent / run_attack_chain_assembly_v2）。
2. 旧 dead-end run_attack_chain_assembly 已删除（含函数本体；worker 注册/导入
   与 workflows 调用由静态导入冒烟间接覆盖）。
"""
from shannon_whitebox.pipeline import activities
from shannon_whitebox.pipeline import workflows


def test_attack_chain_activities_exist():
    assert hasattr(activities, "run_attack_chain_llm_agent")
    assert hasattr(activities, "run_attack_chain_assembly_v2")


def test_old_attack_chain_assembly_removed():
    """旧 dead-end run_attack_chain_assembly 必须删除。"""
    assert not hasattr(activities, "run_attack_chain_assembly"), (
        "旧 run_attack_chain_assembly（dead-end）未删除"
    )
