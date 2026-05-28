# 运行扫描

su -s /bin/bash shannon-user -c 'SHANNON_LOCAL=1; export SHANNON_LOCAL; cd /root/shannon; source .env; node ./shannon start --whitebox-only -r /mnt/d/code/node_futunn_nnq'

# 查看状态

## 看进程是否在跑

ps -u shannon-user

## 看 session 状态（completed = 完成）

cat /root/shannon/workspaces/node_futunn_nnq_whitebox-1779936686738/session.json | grep status

## 看实时日志

tail -f /root/shannon/workspaces/node_futunn_nnq_whitebox-1779936686738/workflow.log

# 恢复中断的扫描

su -s /bin/bash shannon-user -c 'SHANNON_LOCAL=1; export SHANNON_LOCAL; cd /root/shannon; source .env; node ./shannon start --whitebox-only -r /mnt/d/code/node_futunn_nnq -w node_futunn_nnq_whitebox-1779936686738'

# 查看结果

ls /root/shannon/workspaces/node_futunn_nnq_whitebox-1779936686738/deliverables/

# 列出所有 workspace

ls /root/shannon/workspaces/