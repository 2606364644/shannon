# 黑盒 Rerun 人工冒烟

前提：已有跑完的白盒+黑盒 workspace（deliverables 有 evidence + report）。

## 场景 1：默认幂等（已跑过→告知不跑）
1. `shannon-blackbox start --url <url> -r <repo> -w <已有ws>`
2. 预期：输出"该 workspace 已跑过黑盒...如需重跑请加 --rerun"，不启动扫描

## 场景 2：--rerun 强制重跑
1. `shannon-blackbox start --url <url> -r <repo> -w <已有ws> --rerun`
2. 预期：旧 evidence 归档到 `<repo>/.shannon/deliverables/.blackbox-archive/<ts>/`，
   顶层重新生成新 evidence；workflow id 带 `-rerun-<ts>`（Temporal 不报 AlreadyStarted）

## 场景 3：首次黑盒（无 evidence→正常跑）
1. `shannon-blackbox start --url <url> -r <repo> -w <新ws>`（deliverables 无 evidence）
2. 预期：正常跑黑盒，不触发幂等告知
