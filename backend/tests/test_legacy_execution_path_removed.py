"""P0：新任务只能进入 durable execution 路径。"""
from __future__ import annotations

import ast
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_runtime_registers_only_durable_research_entrypoint() -> None:
    execution_source = (
        _BACKEND_ROOT / "app" / "worker" / "execution_worker.py"
    ).read_text(encoding="utf-8")
    celery_source = (
        _BACKEND_ROOT / "app" / "worker" / "celery_app.py"
    ).read_text(encoding="utf-8")

    assert '@celery_app.task(name="tasks.start_research_execution")' in execution_source
    assert '"app.worker.execution_worker"' in celery_source
    assert '"app.worker.harness_worker"' not in celery_source


def test_runtime_callers_do_not_import_legacy_harness_worker() -> None:
    asynchronous_callers = (
        "app/api/routes.py",
        "app/advisor/advisor_routes.py",
        "app/worker/outbox_relay_runner.py",
    )

    for relative_path in asynchronous_callers:
        source = (_BACKEND_ROOT / relative_path).read_text(encoding="utf-8")
        assert "app.worker.harness_worker" not in source
        assert "start_research_execution" in source

    batch_source = (
        _BACKEND_ROOT / "app" / "worker" / "batch_worker.py"
    ).read_text(encoding="utf-8")
    assert "app.worker.harness_worker" not in batch_source
    assert "start_task_execution" in batch_source


def test_task_route_has_no_legacy_execution_branch() -> None:
    source = (_BACKEND_ROOT / "app" / "api" / "routes.py").read_text(encoding="utf-8")

    assert "run_task_pipeline" not in source
    assert "use_harness" not in source
    assert 'execution_mode="legacy"' not in source


def test_durable_report_path_contains_no_placeholder_renderer() -> None:
    source = (
        _BACKEND_ROOT / "app" / "worker" / "execution_worker.py"
    ).read_text(encoding="utf-8")

    assert "本章节依据当前 Gate 与" not in source
    assert "ContactCenterReportComposer" in source


def test_batch_worker_dispatches_only_durable_wrapper_contract() -> None:
    source = (_BACKEND_ROOT / "app" / "worker" / "batch_worker.py").read_text(encoding="utf-8")

    assert "dimensions=dimensions" not in source
    assert "template_id=template_id" not in source
    assert "use_mock_agents=" not in source
    assert "dimension_complexities=" not in source
    assert "skill_id=root_skill_name" in source


def test_batch_dry_run_does_not_call_removed_harness_engine() -> None:
    source = (_BACKEND_ROOT / "app" / "api" / "batch_import_routes.py").read_text(encoding="utf-8")

    assert "execute_harness(" not in source
    assert "SkillRuntimeCatalog().load" not in source
    assert ').load_for_execution("pilot-opportunity"' in source


def test_batch_schedulers_do_not_block_worker_with_sleep() -> None:
    source = (_BACKEND_ROOT / "app" / "worker" / "batch_worker.py").read_text(encoding="utf-8")
    module = ast.parse(source)

    for function_name in ("process_batch", "retry_batch_failed"):
        task = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        function_source = ast.unparse(task)
        assert "time.sleep" not in function_source

    assert "def start_batch_task" in source
    assert "start_batch_task.apply_async" in source
