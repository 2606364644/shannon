# 修复 supernova 清理与启动竞态 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `/root/shannon-py` 下的清理脚本正确识别宿主进程，并让 `up.sh --build` 在 Docker 删除操作短暂进行中时等待而不是直接退出。

**Architecture:** 清理脚本从自身位置计算仓库根目录，不再依赖不存在的 `/root/supernova`。启动脚本保留现有“只清理本 Compose 项目且只处理非运行态容器”的边界，改为逐容器删除并轮询容器消失；只有超过有限重试仍未消失才失败。

**Tech Stack:** Bash、Docker CLI、Docker Compose、ShellCheck（若可用）。

---

## 文件结构

| 文件 | 责任 | 改动 |
|---|---|---|
| `scripts/cleanup-supernova.sh` | 清理宿主进程和 Compose 容器 | 动态计算仓库路径；验证匹配使用动态路径 |
| `scripts/up.sh` | 启动前清理失败/空壳容器 | 删除改为逐容器有限重试，容忍 `removal already in progress` 的短暂竞态 |
| `tests/scripts/test_supernova_scripts.py` | 脚本静态回归测试 | 锁定路径不再写死、删除逻辑有重试/等待 |

## Task 1: 添加失败回归测试

- [ ] 写测试读取两个脚本，确认 cleanup 脚本从 `BASH_SOURCE` 计算仓库根目录，不再包含 `REPO=/root/supernova`。
- [ ] 写测试确认 `up.sh` 有有限重试/等待逻辑，且仍使用 Compose project 过滤和非运行态过滤。
- [ ] 运行测试，确认在现状下因路径和重试逻辑不存在而失败。

## Task 2: 修复路径和 Docker 删除竞态

- [ ] 在 `cleanup-supernova.sh` 中通过 `SCRIPT_DIR` 和 `cd ..` 计算 `REPO`，并将验证阶段的前端路径改为动态路径。
- [ ] 在 `up.sh` 中新增单容器删除等待函数：容器不存在即成功；`docker rm -f` 失败时等待并重试；最多等待固定次数后返回失败。
- [ ] 用该函数替换 `xargs docker rm -f`，避免一个容器的短暂竞态让整个清理批次失控。

## Task 3: 验证

- [ ] 运行 Python 静态回归测试。
- [ ] 运行 `bash -n scripts/cleanup-supernova.sh scripts/up.sh`。
- [ ] 用 `--dry-run` 验证 cleanup 脚本解析出的前端路径为 `/root/shannon-py/packages/web/frontend`。
- [ ] 检查 Compose 项目容器状态后运行 `scripts/up.sh --build`；若环境已有并发 Docker 操作，先确认其结束再重试。
