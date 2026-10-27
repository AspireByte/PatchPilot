"""本机受控任务API；创建只排队，SSE只读取持久化进度。"""

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from patchpilot.service.artifacts import ArtifactStorage
from patchpilot.service.catalog import TaskCatalog
from patchpilot.service.config import ServiceSettings
from patchpilot.service.maintenance import reap_and_cleanup
from patchpilot.service.repository import Repository, StateConflict

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class BudgetRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    max_model_calls: int = Field(default=8, ge=1, le=8)
    max_tool_calls: int = Field(default=32, ge=1, le=32)
    timeout_seconds: float = Field(default=180, ge=1, le=300)
    max_total_tokens: int = Field(default=100000, ge=1, le=100000)
    max_output_tokens: int = Field(default=4096, ge=1, le=4096)


class CreateRun(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    task_id: str = Field(min_length=1, max_length=64)
    mode: Literal["agent", "diagnostic"] = "agent"
    budget: BudgetRequest = Field(default_factory=BudgetRequest)


async def event_stream(repository: Repository, run_id: str, after: int, disconnected):
    cursor = after
    idle = 0
    while not await disconnected():
        try:
            events = await asyncio.to_thread(repository.list_events, run_id, cursor)
            run = await asyncio.to_thread(repository.get_run, run_id)
            if run and run["execution_status"] in TERMINAL and not events:
                # 终态与事件同事务提交，读到终态后必须再排空一次持久化事件。
                events = await asyncio.to_thread(repository.list_events, run_id, cursor)
            for event in events:
                cursor = event["seq"]
                yield f"id: {cursor}\nevent: {event['kind']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except SQLAlchemyError:
            yield 'event: service_error\ndata: {"reason":"database_unavailable"}\n\n'
            return
        if run is None or (run["execution_status"] in TERMINAL and not events):
            return
        idle = 0 if events else idle + 1
        if idle >= 100:
            yield ": keep-alive\n\n"
            idle = 0
        await asyncio.sleep(0.1)


def create_app(settings: ServiceSettings, *, repository: Repository | None = None) -> FastAPI:
    owned = repository is None
    store = repository or Repository(settings.database_url)
    catalog = TaskCatalog(settings.tasks_root)
    artifacts = ArtifactStorage(settings.data_root / "artifacts")

    @asynccontextmanager
    async def lifespan(app):
        async def reaper():
            while True:
                try:
                    await asyncio.to_thread(
                        reap_and_cleanup, store, cleanup=settings.cleanup_expired
                    )
                except SQLAlchemyError:
                    pass
                await asyncio.sleep(settings.reap_seconds)

        task = asyncio.create_task(reaper())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if owned:
                store.close()

    def authenticate(authorization: Annotated[str | None, Header()] = None):
        expected = f"Bearer {settings.api_token}".encode()
        if authorization is None or not secrets.compare_digest(authorization.encode(), expected):
            raise HTTPException(
                401, "需要有效的本机服务令牌", headers={"WWW-Authenticate": "Bearer"}
            )

    app = FastAPI(
        title="PatchPilot 任务服务", lifespan=lifespan, dependencies=[Depends(authenticate)]
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.exception_handler(StateConflict)
    async def conflict(request, error):
        return Response(
            json.dumps({"detail": "当前任务状态不允许该操作"}, ensure_ascii=False),
            status_code=409,
            media_type="application/json",
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_failure(request, error):
        return Response(
            '{"detail":"数据库暂不可用"}', status_code=503, media_type="application/json"
        )

    def required(run_id: UUID):
        run = store.get_run(str(run_id))
        if run is None:
            raise HTTPException(404, "任务不存在")
        return run

    @app.get("/v1/tasks")
    def tasks():
        return catalog.public_tasks()

    @app.post("/v1/runs", status_code=201)
    def create(payload: CreateRun):
        try:
            task = catalog.load(payload.task_id)
        except (ValueError, OSError):
            raise HTTPException(422, "不支持的任务或任务快照不合法") from None
        return store.create_run(
            payload.task_id, payload.mode, payload.budget.model_dump(), task.spec.snapshot_sha256
        )

    @app.get("/v1/runs")
    def history(limit: Annotated[int, Query(ge=1, le=100)] = 50):
        return store.list_runs(limit)

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: UUID):
        return required(run_id)

    @app.get("/v1/runs/{run_id}/attempts")
    def attempts(run_id: UUID):
        required(run_id)
        return store.list_attempts(str(run_id))

    @app.get("/v1/runs/{run_id}/steps")
    def steps(run_id: UUID):
        required(run_id)
        return store.list_steps(str(run_id))

    @app.get("/v1/runs/{run_id}/artifacts")
    def list_artifacts(run_id: UUID):
        required(run_id)
        return [
            {key: item[key] for key in ("id", "attempt_id", "kind", "sha256", "size")}
            for item in store.list_artifacts(str(run_id))
        ]

    def download(run_id: UUID, reference: dict | None):
        if reference is None or reference["kind"] not in (
            "candidate_patch",
            "report",
            "checkpoint_patch",
            "log",
        ):
            raise HTTPException(404, "产物不存在或不支持下载")
        try:
            content = artifacts.read(reference)
        except (ValueError, OSError):
            raise HTTPException(409, "产物缺失或内容校验失败") from None
        media = "application/json" if reference["kind"] == "report" else "text/plain; charset=utf-8"
        return Response(
            content, media_type=media, headers={"X-Content-SHA256": reference["sha256"]}
        )

    @app.get("/v1/runs/{run_id}/artifacts/{artifact_id}")
    def artifact(run_id: UUID, artifact_id: UUID):
        required(run_id)
        return download(run_id, store.get_artifact(str(run_id), str(artifact_id)))

    def current_artifact(run_id, kind):
        run = required(run_id)
        report_reference = next(
            (
                item
                for item in store.list_artifacts(str(run_id))
                if item["attempt_id"] == run["current_attempt_id"] and item["kind"] == "report"
            ),
            None,
        )
        if kind == "report" or report_reference is None:
            return download(run_id, report_reference)
        try:
            report = json.loads(artifacts.read(report_reference))
            patch_id = str(UUID(report["patch_artifact_id"]))
            reference = store.get_artifact(str(run_id), patch_id)
        except (ValueError, OSError, KeyError, TypeError):
            raise HTTPException(409, "最终报告的补丁引用不合法") from None
        if (
            reference is None
            or reference["kind"] != "candidate_patch"
            or reference["attempt_id"] != run["current_attempt_id"]
        ):
            raise HTTPException(409, "最终补丁与当前Attempt不一致")
        return download(run_id, reference)

    @app.get("/v1/runs/{run_id}/patch")
    def patch(run_id: UUID):
        return current_artifact(run_id, "candidate_patch")

    @app.get("/v1/runs/{run_id}/report")
    def report(run_id: UUID):
        return current_artifact(run_id, "report")

    @app.post("/v1/runs/{run_id}/cancel")
    def cancel(run_id: UUID):
        required(run_id)
        return store.cancel(str(run_id))

    @app.post("/v1/runs/{run_id}/restart", status_code=202)
    def restart(run_id: UUID):
        required(run_id)
        return store.restart(str(run_id))

    @app.get("/v1/runs/{run_id}/events")
    async def events(
        request: Request,
        run_id: UUID,
        after: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ):
        required(run_id)
        if last_event_id is not None:
            try:
                after = int(last_event_id)
                if not 0 <= after <= 2**63 - 1:
                    raise ValueError
            except ValueError:
                raise HTTPException(400, "Last-Event-ID必须是非负事件序号") from None
        return StreamingResponse(
            event_stream(store, str(run_id), after, request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
