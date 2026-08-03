"""
Extractor Agent - 信息提取智能体

从搜索结果中结构化提取证据
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from app.llm.gateway_client import get_gateway_client, GatewayClient
from app.agents.harness.state import SearchResult, Evidence
from app.agents.harness.extraction_batch import ExtractionBatch
from app.agents.schemas.batch_extraction_schema import (
    BatchExtractionItem,
    BatchExtractionResponse,
)

logger = logging.getLogger(__name__)

# 提示词模板路径
PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "bidding.md")
BATCH_PROMPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "prompts", "batch_extraction.md"
)


class BatchExtractionSchemaError(RuntimeError):
    """批量提取响应不满足 v1 合同时抛出。"""


@dataclass(frozen=True)
class BatchExtractionResult:
    """一次模型调用的批量提取结果；缺失项由后续最小重试步骤处理。"""

    items_by_candidate_id: dict[str, BatchExtractionItem]
    missing_candidate_ids: tuple[str, ...]
    model: str
    provider: str
    usage: dict
    finish_reason: str
    max_output_tokens: int
    timeout_seconds: int


@dataclass(frozen=True)
class BatchExtractionRetryResult:
    """最小重试后的汇总结果；达到上限的候选带有明确拒绝原因。"""

    items_by_candidate_id: dict[str, BatchExtractionItem]
    rejected_by_candidate_id: dict[str, str]
    retried_candidate_ids: tuple[str, ...]
    attempt_count: int


class ExtractorAgent:
    """
    信息提取智能体

    职责:
    - 从搜索结果中结构化提取证据
    - 输出统一的 Evidence 格式
    """

    # 通用提取提示词模板
    DEFAULT_SYSTEM_PROMPT = """
你是信息提取专家。请从给定的网页内容中提取与挖掘目标相关的结构化信息。

输出格式要求:
1. 严格输出 JSON 格式（不要包含 Markdown code block）
2. 如果提取到多条信息，输出 JSON 数组
3. 如果仅提取到一条信息，输出 JSON 对象

每个证据至少包含:
- title: 标题
- snippet: 摘要/关键信息
- metadata: 包含其他提取字段的字典

示例输出:
```json
[
  {
    "title": "项目名称",
    "snippet": "项目简介摘要",
    "metadata": {
      "采购人": "XXX 单位",
      "中标金额": "100 万元",
      "发布时间": "2025-01-15"
    }
  }
]
```

如果无法提取到有效信息，输出空数组 []。
"""

    def __init__(
        self,
        llm_client: Optional[GatewayClient] = None,
        system_prompt: Optional[str] = None,
        token_tracker=None,
        model: Optional[str] = None,
    ):
        self.llm_client = llm_client or get_gateway_client()
        self.token_tracker = token_tracker
        self.system_prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.model = model

    def execute(
        self,
        results: list[SearchResult],
        must_extract: list[str],
        dimension: str
    ) -> list[Evidence]:
        """
        从搜索结果中提取证据

        Args:
            results: 搜索结果列表
            must_extract: 必填字段列表
            dimension: 维度名称

        Returns:
            list[Evidence]
        """
        evidences = []

        for i, result in enumerate(results):
            logger.info(f"[ExtractorAgent] 处理结果 {i + 1}/{len(results)}")

            # 构建提取内容
            content = self._build_content(result)

            if not content.strip():
                continue

            # 调用 LLM 提取
            extracted = self._extract(content, must_extract)

            # 转换为 Evidence
            if extracted:
                evidence = self._convert_to_evidence(extracted, dimension, result)
                if evidence:
                    evidences.append(evidence)

        logger.info(f"[ExtractorAgent] 提取完成，共{len(evidences)}条证据")

        return evidences

    def execute_batch(
        self,
        batch: ExtractionBatch,
        must_extract: list[str],
        *,
        reference_context: Sequence[Mapping[str, object]] = (),
        max_output_tokens: int = 16_000,
        timeout_seconds: int = 120,
    ) -> BatchExtractionResult:
        """对一个已规划批次只调用一次模型，并按 candidate_id 返回提取结果。

        该方法只执行协议 v1 的批处理调用，不将数组位置视为候选标识；未返回的
        输入 ID 被显式暴露给 TEO-03-04 的最小重试机制，绝不静默补造结果。
        """
        if not isinstance(batch, ExtractionBatch):
            raise TypeError("batch 必须为 ExtractionBatch")
        if type(max_output_tokens) is not int or max_output_tokens < 1:
            raise ValueError("max_output_tokens 必须为正整数")
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ValueError("timeout_seconds 必须为正整数")
        input_ids = tuple(candidate.candidate_id for candidate in batch.candidates)
        if not input_ids:
            return BatchExtractionResult(
                items_by_candidate_id={},
                missing_candidate_ids=(),
                model="",
                provider="",
                usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                finish_reason="not_called_empty_batch",
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
            )
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("批量提取输入不允许重复 candidate_id")

        response = self._call_batch_model(
            batch,
            must_extract,
            reference_context=reference_context,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        try:
            parsed = BatchExtractionResponse.from_dict(
                json.loads(str(response.get("content") or "")),
                required_fields=must_extract,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            if str(response.get("finish_reason") or "stop") == "length":
                raise BatchExtractionSchemaError("批量提取输出被 Provider 截断") from error
            raise BatchExtractionSchemaError(f"批量提取响应不符合 v1 合同: {error}") from error

        item_by_id = {item.candidate_id: item for item in parsed.items}
        unknown_ids = set(item_by_id) - set(input_ids)
        if unknown_ids:
            raise BatchExtractionSchemaError(
                f"批量提取响应包含未输入的 candidate_id: {sorted(unknown_ids)}"
            )
        usage = self._record_batch_usage(response)
        return BatchExtractionResult(
            items_by_candidate_id=item_by_id,
            missing_candidate_ids=tuple(candidate_id for candidate_id in input_ids if candidate_id not in item_by_id),
            model=str(response.get("model") or ""),
            provider=str(response.get("provider") or ""),
            usage=dict(usage),
            finish_reason=str(response.get("finish_reason") or "stop"),
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )

    def execute_batch_with_minimal_retry(
        self,
        batch: ExtractionBatch,
        must_extract: list[str],
        *,
        reference_context: Sequence[Mapping[str, object]] = (),
        max_batch_retries: int = 1,
        max_output_tokens: int = 16_000,
        timeout_seconds: int = 120,
    ) -> BatchExtractionRetryResult:
        """只重试未返回或可归属的非法候选，已成功项不再进入后续请求。"""
        if type(max_batch_retries) is not int or max_batch_retries < 0:
            raise ValueError("max_batch_retries 必须为非负整数")
        if not isinstance(batch, ExtractionBatch):
            raise TypeError("batch 必须为 ExtractionBatch")
        input_by_id = {candidate.candidate_id: candidate for candidate in batch.candidates}
        if len(input_by_id) != len(batch.candidates):
            raise ValueError("批量提取输入不允许重复 candidate_id")
        pending_reasons = {candidate_id: "模型未返回该候选" for candidate_id in input_by_id}
        accepted_items: dict[str, BatchExtractionItem] = {}
        retried_ids: list[str] = []
        attempts = 0

        while pending_reasons and attempts <= max_batch_retries:
            pending_ids = tuple(pending_reasons)
            if attempts:
                retried_ids.extend(pending_ids)
            current_batch = ExtractionBatch(
                index=batch.index,
                candidates=tuple(input_by_id[candidate_id] for candidate_id in pending_ids),
                estimated_input_tokens=sum(
                    len(input_by_id[candidate_id].title + input_by_id[candidate_id].content) // 2
                    for candidate_id in pending_ids
                ),
                estimated_output_tokens=batch.estimated_output_tokens,
                constraint_limited=len(pending_ids) < len(batch.candidates),
            )
            response = self._call_batch_model(
                current_batch,
                must_extract,
                reference_context=reference_context,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
                retry_attempt=attempts,
            )
            attempts += 1
            try:
                items, pending_reasons = self._parse_retryable_items(
                    str(response.get("content") or ""),
                    pending_ids,
                    required_fields=must_extract,
                )
            except BatchExtractionSchemaError as error:
                if str(response.get("finish_reason") or "stop") == "length":
                    raise BatchExtractionSchemaError("批量提取输出被 Provider 截断，无法安全识别成功项") from error
                raise
            self._record_batch_usage(response)
            accepted_items.update(items)

        rejected = {
            candidate_id: f"{reason}；已达到批量提取最小重试上限"
            for candidate_id, reason in pending_reasons.items()
        }
        return BatchExtractionRetryResult(
            items_by_candidate_id=accepted_items,
            rejected_by_candidate_id=rejected,
            retried_candidate_ids=tuple(retried_ids),
            attempt_count=attempts,
        )

    def _call_batch_model(
        self,
        batch: ExtractionBatch,
        must_extract: list[str],
        *,
        reference_context: Sequence[Mapping[str, object]],
        max_output_tokens: int,
        timeout_seconds: int,
        retry_attempt: int = 0,
    ) -> dict:
        return self.llm_client.infer(
            prompt=self._build_batch_extraction_prompt(
                batch,
                must_extract,
                reference_context=reference_context,
                retry_attempt=retry_attempt,
            ),
            model=self.model,
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            max_retries=0,
            thinking_mode="disabled",
        )

    def _record_batch_usage(self, response: dict) -> dict:
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        total_tokens = usage.get("total_tokens")
        if self.token_tracker and isinstance(total_tokens, (int, float)):
            self.token_tracker.record_usage("extraction", int(total_tokens))
        return dict(usage)

    @staticmethod
    def _parse_retryable_items(
        content: str,
        expected_ids: tuple[str, ...],
        *,
        required_fields: Sequence[str] = (),
    ) -> tuple[dict[str, BatchExtractionItem], dict[str, str]]:
        """保留合法项，将能定位到 ID 的非法项交给下一最小批；其余结构错误硬失败。"""
        try:
            raw_response = json.loads(content)
        except json.JSONDecodeError as error:
            raise BatchExtractionSchemaError("批量提取 JSON 无法解析，无法安全最小重试") from error
        if not isinstance(raw_response, dict) or set(raw_response) != {"items"}:
            raise BatchExtractionSchemaError("批量提取顶层结构非法，无法安全最小重试")
        raw_items = raw_response.get("items")
        if not isinstance(raw_items, list):
            raise BatchExtractionSchemaError("批量提取 items 非数组，无法安全最小重试")

        expected_id_set = set(expected_ids)
        accepted: dict[str, BatchExtractionItem] = {}
        retry_reasons = {candidate_id: "模型未返回该候选" for candidate_id in expected_ids}
        seen_ids: set[str] = set()
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                raise BatchExtractionSchemaError("批量提取 item 非对象，无法安全最小重试")
            candidate_id = raw_item.get("candidate_id")
            if not isinstance(candidate_id, str) or candidate_id not in expected_id_set:
                raise BatchExtractionSchemaError("批量提取包含未知或不可识别 candidate_id")
            if candidate_id in seen_ids:
                accepted.pop(candidate_id, None)
                retry_reasons[candidate_id] = "模型重复返回该候选"
                continue
            seen_ids.add(candidate_id)
            try:
                item = BatchExtractionItem.from_dict(
                    raw_item,
                    required_fields=required_fields,
                )
            except ValueError as error:
                retry_reasons[candidate_id] = f"模型返回的候选项非法: {error}"
                continue
            accepted[candidate_id] = item
            retry_reasons.pop(candidate_id, None)
        return accepted, retry_reasons

    @staticmethod
    def _build_batch_extraction_prompt(
        batch: ExtractionBatch,
        must_extract: list[str],
        *,
        reference_context: Sequence[Mapping[str, object]] = (),
        retry_attempt: int = 0,
    ) -> str:
        """使用固定 v1 模板生成紧凑批量请求，不携带人工标签。"""
        with open(BATCH_PROMPT_PATH, "r", encoding="utf-8") as prompt_file:
            template = prompt_file.read()
        candidates = [
            {
                "candidate_id": candidate.candidate_id,
                "title": candidate.title,
                "content": candidate.content,
            }
            for candidate in batch.candidates
        ]
        prompt = template.replace(
            "{{required_fields_json}}",
            json.dumps(list(must_extract), ensure_ascii=False),
        ).replace(
            "{{candidates_json}}",
            json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
        )
        if reference_context:
            prompt = (
                "<skill_references>\n"
                + json.dumps(
                    list(reference_context),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n</skill_references>\n\n"
                + prompt
            )
        if retry_attempt:
            return prompt.replace(
                "<candidates>",
                "<retry_instruction>这是第 "
                f"{retry_attempt} 次最小重试。仅处理当前候选并严格遵守输出合同。</retry_instruction>\n\n"
                "<candidates>",
            )
        return prompt

    def _build_content(self, result: SearchResult) -> str:
        """构建提取内容"""
        # 优先使用抓取的全文，否则使用 snippet
        if result.raw_content:
            content = result.raw_content[:5000]  # 限制长度
        else:
            content = result.snippet

        return f"标题：{result.title}\n来源：{result.url}\n内容:\n{content}"

    @staticmethod
    def convert_batch_item_to_evidence(
        item: BatchExtractionItem,
        *,
        dimension: str,
        result: SearchResult,
        candidate_id: str,
        fetch_content_quality: str,
        fetch_confidence: float,
    ) -> Optional[Evidence]:
        """将批提取成功项转换为可追溯 Evidence；显式拒绝项不伪造证据。"""
        if not item.fields:
            return None
        fields = dict(item.fields)
        title = fields.pop("title", None) or fields.pop("项目名称", None) or result.title
        metadata = {
            **fields,
            "candidate_id": candidate_id,
            "batch_extraction_confidence": item.confidence,
            "fetch_content_quality": fetch_content_quality,
            "fetch_confidence": fetch_confidence,
        }
        if item.truncated_field_names:
            metadata.update(
                {
                    "batch_extraction_original_field_count": item.original_field_count,
                    "batch_extraction_truncated_field_names": list(
                        item.truncated_field_names
                    ),
                }
            )
        if result.raw_content:
            metadata["_raw_content"] = result.raw_content
        return Evidence(
            dimension=dimension,
            title=str(title)[:200],
            snippet=item.citation_excerpt[:1000],
            url=result.url,
            source_type="batch_extraction",
            metadata=metadata,
            published_at=result.date,
        )

    def _extract(
        self,
        content: str,
        must_extract: list[str]
    ) -> Optional[dict]:
        """调用 LLM 提取信息"""
        # 构建提示词
        user_prompt = self._build_extraction_prompt(content, must_extract)

        response = self.llm_client.infer(
            prompt=user_prompt,
            system_prompt=self.system_prompt,
            model=self.model,
            response_format={"type": "json_object"},
            temperature=0.3  # 较低温度保证提取稳定性
        )

        try:
            data = json.loads(response["content"])

            # 记录 token 使用
            tokens_used = response["usage"]["total_tokens"]
            if self.token_tracker:
                self.token_tracker.record_usage("extraction", tokens_used)

            # 处理数组或单对象
            if isinstance(data, list):
                return data[0] if data else None
            elif isinstance(data, dict):
                return data
            else:
                return None
        except json.JSONDecodeError as e:
            logger.error(f"[ExtractorAgent] JSON 解析失败：{e}")
            return None

    def _build_extraction_prompt(
        self,
        content: str,
        must_extract: list[str]
    ) -> str:
        """构建提取提示词"""
        parts = [f"请从以下内容中提取信息：\n\n{content}"]

        if must_extract:
            parts.append(f"\n必填字段：{', '.join(must_extract)}")

        return "\n".join(parts)

    def _convert_to_evidence(
        self,
        extracted: dict,
        dimension: str,
        result: SearchResult
    ) -> Optional[Evidence]:
        """将提取结果转换为 Evidence

        将 result.raw_content 通过 metadata["_raw_content"] 传递给
        harness_worker，用于 SnapshotService 保存原始内容快照。
        """
        title = extracted.get("title") or extracted.get("项目名称") or result.title
        snippet = extracted.get("snippet") or extracted.get("项目简介") or result.snippet

        # 移除已提取的字段，其余放入 metadata
        metadata = {k: v for k, v in extracted.items() if k not in ["title", "snippet"]}

        # WBS-6: 传递原始内容用于快照保存
        if result.raw_content:
            metadata["_raw_content"] = result.raw_content

        return Evidence(
            dimension=dimension,
            title=str(title)[:200],
            snippet=str(snippet)[:1000],
            url=result.url,
            source_type="web_scrape",
            metadata=metadata
        )
