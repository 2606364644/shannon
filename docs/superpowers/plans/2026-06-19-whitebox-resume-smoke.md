# 白盒 Resume 人工冒烟

## 场景 1：auto resume（中断后续扫）
1. `shannon-whitebox start -r <repo> -w smoke-ws`，跑到 pre-recon 完成后 Ctrl+C
2. 确认 `<repo>/.shannon/deliverables/pre_recon_deliverable.md` 存在 + git log 有 `deliverable: pre-recon`
3. 重跑 `shannon-whitebox start -r <repo> -w smoke-ws`
4. 预期：workflow id = `smoke-ws-resume-1`，pre-recon 被跳过，从 recon 开始

## 场景 2：--rewind
1. 完整跑完一次 `-w smoke-ws2`
2. `shannon-whitebox start -r <repo> -w smoke-ws2 --rewind recon`
3. 预期：pre-recon 跳过，recon 及之后重跑；旧 recon/vuln deliverable 归档到 `.whitebox-archive/<ts>/`

## 场景 3：--fresh
1. `shannon-whitebox start -r <repo> -w smoke-ws --fresh`
2. 预期：忽略历史全新扫，workflow id 不带 resume 计数
