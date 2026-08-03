# 候选筛选 POC 标注指南

> 适用 Schema：`task-screening-fixture/v5`
> 适用范围：TEO-00 离线 POC，不写生产数据库

## 1. v5 候选身份簇

Fixture 导出后先按标题、URL 和项目锚点进行保守、非传递聚合。模型只接收每个身份簇的代表候选，原始候选 ID 全部保存在 `candidate_identity_clusters` 中。

- `original_candidate_count`：聚合前候选数。
- `candidate_count`：代表候选数。
- `identity_key`：身份簇稳定标识。
- `representative_id`：模型接收的候选 ID。
- `member_ids`：代表项及全部别名 ID。
- `match_basis`：相同 URL/标题、跨 URL 标题包含等聚合依据。
- `annotation_resolution`：同簇旧标注、最终标注和纠错原因。

同 URL 但标题属于不同项目时不得合并；相似标题不能通过第三条候选产生链式合并。`annotation_status=completed` 前，每个身份簇必须完成标注冲突处理。

## 2. 目标主体口径

`target_scope_policy` 固定为 `specified_entity_and_parent`：

- `target_entity_names`：任务指定主体及其常用名称。
- `target_parent_names`：指定主体的上级总行、总部或集团。
- 其他地区分支、下级子公司不属于目标主体，客服相关内容归为行业能力情报。

例如，任务主体为邮储上海分行时，邮储总行属于上级主体，深圳、重庆等其他分行属于外部行业样本。

## 3. 业务标签与证据角色

| `business_label` | 含义 | `evidence_group` |
| --- | --- | --- |
| `must_keep` | 不可替代的关键证据 | 禁止填写 |
| `relevant` | 某一证据组的标准来源 | 必填 |
| `acceptable_alternative` | 可替代同组标准来源 | 必填 |
| `irrelevant` | 与研究目标无关 | 禁止填写 |
| `uncertain` | 当前信息不足 | 禁止填写 |

每个 `evidence_group` 必须恰好一条 `relevant`。

| `evidence_role` | 含义 |
| --- | --- |
| `active_target_opportunity` | 指定主体/上级正在有效期内的客服核心采购 |
| `target_procurement` | 指定主体/上级的客服相关历史或状态未知采购 |
| `target_operation_signal` | 指定主体/上级的客服相邻运营信号 |
| `industry_capability_intelligence` | 其他分支、子公司或外部机构的客服能力情报 |
| `vendor_case_intelligence` | 厂商公开案例或解决方案 |
| `out_of_scope` | 与客服研究方向无关 |
| `uncertain` | 无法可靠分类 |

`procurement_lifecycle` 只能为 `active`、`closed_or_failed`、`historical_or_unknown`、`not_applicable`。只有 `active_target_opportunity` 可以使用 `active` 和 `active_until`；有效期必须来自公告明确截止时间，不能从发布时间推断。

## 4. 身份簇冲突处理

同一身份簇出现不同旧标签时，优先级固定为：

`must_keep > relevant > acceptable_alternative > uncertain > irrelevant`

- 正向证据与无关标签冲突时采用正向标签，并在 `annotation_resolution` 保留全部旧值。
- `relevant` 与其重复来源合并后只保留一个代表候选。
- 聚合导致证据组缺少 `relevant` 时，将组内优先级最高的代表候选提升为 `relevant`。
- 指定主体的反诈、装修、空调、布线等非客服采购必须为 `out_of_scope`。
- `is_gold_reference` 仅表示历史报告引用，不得作为质量答案。

## 5. 提交检查

- Schema 为 `task-screening-fixture/v5`，且 `annotation_status=completed`。
- 代表候选、身份簇和原始数量完全一致，一个原始 ID 只属于一个簇。
- 别名 ID 不再出现在 `candidates`。
- 每个身份簇的 `annotation_resolution.status=resolved`，并与代表候选最终标签一致。
- 所有候选具有合法 `business_label`、`evidence_role` 和 `procurement_lifecycle`。
- 每个证据组恰好一条 `relevant`，`uncertain` 占比不超过10%。
- 人工标签、历史引用字段不会进入模型 Prompt。
