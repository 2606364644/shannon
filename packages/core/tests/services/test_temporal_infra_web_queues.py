"""WEB 固定 task queue 常量：web 提交端与 worker 消费端的单一来源。

CLI 路径用 generate_task_queue(prefix) 生成唯一随机 queue（self-contained）；
WEB 路径用固定 queue，worker 容器常驻消费。两者前缀同但值不同，互不消费。"""
from supernova_core.services.temporal_infra import (
    generate_task_queue,
    WEB_TASK_QUEUE_WHITEBOX,
    WEB_TASK_QUEUE_BLACKBOX,
)


def test_web_task_queue_constants_are_fixed_strings():
    """WEB queue 是固定值（非随机），worker 容器据此常驻注册。"""
    assert WEB_TASK_QUEUE_WHITEBOX == "supernova-wb-web"
    assert WEB_TASK_QUEUE_BLACKBOX == "supernova-bb-web"


def test_web_queues_distinct_from_cli_random_queues():
    """WEB 固定 queue 与 CLI 随机 queue 不同——worker 注册固定 queue 不会收到 CLI 提交。"""
    cli_wb = generate_task_queue("supernova-wb")
    cli_bb = generate_task_queue("supernova-bb")
    assert cli_wb != WEB_TASK_QUEUE_WHITEBOX  # 随机 hex 后缀 vs 固定 -web
    assert cli_bb != WEB_TASK_QUEUE_BLACKBOX
    assert not WEB_TASK_QUEUE_WHITEBOX.endswith(cli_wb[-8:])


def test_web_queues_whitebox_and_blackbox_distinct():
    """白盒/黑盒 WEB queue 不同，两个 worker 各消费各的。"""
    assert WEB_TASK_QUEUE_WHITEBOX != WEB_TASK_QUEUE_BLACKBOX
