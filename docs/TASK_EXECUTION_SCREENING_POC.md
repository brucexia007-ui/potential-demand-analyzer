# 候选筛选 POC 与 G1 决策

> **文档定位：历史 v3 POC 过程记录，非当前决策依据。**当前 Fixture 为 `task-screening-fixture/v5`、POC 输出协议为 `task-screening-poc/v6`；三份真实复测审计的机器门禁均为 `FAIL`。业务方已作出 `MANUAL_CONDITIONAL_PASS`，仅解除后续开发与旁路影子验证依赖，生产筛选保持关闭，不得影响用户候选、抓取、提取或报告。权威结论见 [TEO-00 G1 三样本复测结果](./TEO-00-G1-RETEST-RESULTS.md) 与 [任务执行优化 WBS](./TASK_EXECUTION_OPTIMIZATION_WBS.md)。

> **最后校准：2026-07-18。**达到 10 个完成人工标注样本、50 个影子任务或人工裁决满 30 天任一条件后，必须以扩充样本重新评审，不能以本文件所述历史 v3 指标启用生产筛选。

> 状态：**BLOCKED——评分卡协议与三样本第一轮初标已完成，等待业务专家二次复核与真实模型复测**
> 当前结论：**G1 尚未通过，继续阻断 TEO-01 / TEO-02 生产实现**
> 最后更新：2026-07-15
> WBS：`TEO-00-05`、`TEO-00-06`、`TEO-00-07`

## 1. 本轮优化结果

POC 已从“历史引用作为金标、模型直接返回 Top20”升级为：

1. `task-screening-fixture/v3` 全量五级业务标注；
2. Single 一次输入全部紧凑候选；
3. 模型为每个候选输出完整评分卡；
4. 程序按固定排序键生成 Top20；
5. 使用关键证据、证据组、已判定精确率和 Top20 重合率完成 G1 判断。

`is_gold_reference` 仅保留历史报告引用来源的审计意义，不再进入质量指标。Fixture v2 和旧 `items/rank` 响应协议均被拒绝，不保留运行兼容路径。

## 2. 新协议配置

| 项目 | 值 |
| --- | --- |
| Fixture Schema | `task-screening-fixture/v3` |
| POC Result Schema | `task-screening-poc/v2` |
| Screening Protocol | `full_scorecard_deterministic_top_k/v1` |
| 策略 | `single_scorecard` |
| 候选规模 | 常规59～80条一次输入；压力样本可超过80条 |
| Top K | 20 |
| 位置视图 | 前、中、后三个确定性旋转视图 |
| 模型 | `deepseek-v4-pro` |
| 思考模式 / Temperature | `disabled` / `0` |
| 最大输出 Token | 8,000 / 次物理上限；4,000 / 次软提示线 |
| 单次超时 / SDK 重试 | 60 秒 / 0 次 |
| 结果审计 | 每个视图保留完整评分卡、Top20、usage、模型、Provider、结束原因、响应长度和错误 |

模型输出每个输入 ID 恰好一条评分卡，包含 `relevance`、`source_quality`、`evidence_type`、`novelty` 和 `reason_code`。缺失、重复、未知 ID、越界值、非法枚举或 JSON 截断全部判为 Schema 失败，不静默补偿。

确定性排序键为：

`relevance` 降序 → `evidence_type` 优先级 → `source_quality` 降序 → `novelty` 降序 → 发布时间降序 → `candidate_id` 升序。

## 3. G1 门禁

单个样本必须同时满足：

| 指标 | 门槛 |
| --- | ---: |
| Must-keep Recall@20 | 每个位置视图均为 100% |
| Evidence Group Recall@20 | 三视图平均 ≥90% |
| Judged Precision@20 | 每个位置视图均 ≥85% |
| Top20 Overlap | 任意两视图交集 / 20 ≥85% |
| Schema 成功率 | ≥99% |
| 费用 | 输入、输出单价齐全且费用可计算 |

Token 采用“质量优先、软提示”策略：默认给单次调用 `max_output_tokens=8000` 的物理生成空间，`4000` 只是输出 Token 提示线。实际用量超过提示线时继续解析和评测，不暂停任务、不拦截结果，也不影响 G1；审计结果记录 `token_budget_status=warning_exceeded`。只有 Provider 在物理上限截断并造成评分卡不完整时，才按 Schema 失败处理。

Jaccard 和 NDCG 继续记录，但 Jaccard 不再作为门禁。NDCG 权重为 `must_keep=3`、`relevant=2`、`acceptable_alternative=1`、其他为 0。

最终 G1 只有在 3 个不同复杂度的历史样本全部通过时才通过；任一样本、任一位置视图遗漏 `must_keep` 都维持 No-Go。

## 4. 历史基线说明

旧 Fixture v2 的标注版 Single 结果为：Critical Recall 93.33%、历史引用 Recall 57.14%、Schema 100%、Jaccard 0.739、Top20 实际重合率 85%。该结果暴露了两类问题：

- `c_0003` 在一个位置视图中漏选，说明模型直接 Top20 存在位置敏感性；
- 员工慰问品、空调采购、媒体活动等历史引用被错误纳入质量金标，而部分智能客服/呼叫中心替代证据被错误当成误选。

这些结果仅作为评分卡协议前的历史基线，不可与 v3 新指标直接合并，也不能用于解除 G1。

Chunked 历史结果在质量、调用数和 Token 上均劣于 Single，本轮已从运行器移除；只有 Single 通过三样本 G1 后才单独重新设计和评估 Chunked。

## 5. 当前阻塞项

1. 业务专家需要按 [候选筛选 POC 全量标注指南](./TASK_SCREENING_ANNOTATION_GUIDE.md) 复核三份 `pending_review` 初标稿，并将确认后的文件另存为 `.fixture.v3.annotated.json`。
2. 需要填写 DeepSeek 合同或账单中的输入、输出单价，完成费用门。
3. 三个样本需分别运行三个位置视图并保存独立结果文件。

在以上条件完成前，`TEO-00-07` 保持 `BLOCKED`，不得进入生产筛选集成。

已导出的三份标注模板：

| 样本 | 人工复核入口 | 候选数 | 初标状态 | 用途 |
| --- | --- | ---: | ---: | --- |
| 上海银行客服中心系统建设 | `task-7a8d91ba-bidding.fixture.v3.preannotated.json` | 59 | `pending_review` | 常规基准 |
| 邮储银行上海分行智能客服 | `task-9e58e370-postal-bidding.fixture.v3.preannotated.json` | 50 | `pending_review` | 较低复杂度对照 |
| 太平洋保险智能客服/呼叫中心国产化 | `task-3f42621a-pacific-bidding.fixture.v3.preannotated.json` | 137 | `pending_review` | 长上下文压力样本；不改变生产候选上限 |

## 6. v3 重跑命令

在项目根目录执行脚本和样本复制：

```powershell
docker cp backend/scripts/run_task_screening_poc.py `
  potential-demand-backend:/app/run_task_screening_poc.py

docker cp backend/data/poc/task-7a8d91ba-bidding.fixture.v3.annotated.json `
  potential-demand-backend:/tmp/task-7a8d91ba-bidding.fixture.v3.annotated.json
```

在已确认历史候选数据可发送至目标 Provider 后运行：

```powershell
docker exec -w /app potential-demand-backend `
  python /app/run_task_screening_poc.py `
  /tmp/task-7a8d91ba-bidding.fixture.v3.annotated.json `
  --output /tmp/task-7a8d91ba-bidding.single-scorecard.poc.json `
  --top-k 20 `
  --max-output-tokens 8000 `
  --output-token-warning-threshold 4000 `
  --model deepseek-v4-pro `
  --thinking-mode disabled `
  --call-timeout-seconds 60 `
  --max-retries 0 `
  --input-price-per-million $env:DEEPSEEK_INPUT_PRICE_PER_MILLION `
  --output-price-per-million $env:DEEPSEEK_OUTPUT_PRICE_PER_MILLION
```

复制独立审计结果：

```powershell
docker cp potential-demand-backend:/tmp/task-7a8d91ba-bidding.single-scorecard.poc.json `
  backend/data/poc/task-7a8d91ba-bidding.single-scorecard.poc.json
```
