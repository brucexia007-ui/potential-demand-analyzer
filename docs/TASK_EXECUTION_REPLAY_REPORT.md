# 任务执行离线回放报告

状态：待采集历史事件后执行。

回放器只读取导出的 `TaskEvent` JSON，不连接或写入生产数据库。

```powershell
docker exec -w /app potential-demand-backend `
  python scripts/replay_task_execution.py /tmp/task-events.json `
  --output /tmp/task-execution-replay-report.json
```

验收时应归档：输入事件哈希、回放终态、序列连续性、重复完成诊断、未完成工作单元和与数据库当前终态的差异。
