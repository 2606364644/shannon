"""whitebox worker activity 注册守卫。"""
from pathlib import Path

WORKER_FILE = (
    Path(__file__).resolve().parents[1]
    / "src" / "shannon_whitebox" / "worker.py"
)


def test_worker_registers_log_info_activity():
    """防回归：log_info_activity 必须在 worker.py 注册（import + activities 列表）。

    见 temporalio-activity-worker-registration 教训。
    """
    worker_src = WORKER_FILE.read_text()
    count = worker_src.count("log_info_activity")
    assert count >= 2, (
        f"log_info_activity 在 worker.py 仅出现 {count} 次，预期 >= 2"
        "（import 一处 + activities 列表一处）。"
    )
