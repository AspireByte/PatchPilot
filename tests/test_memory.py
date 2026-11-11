"""长期记忆的隔离、原子更新、中文检索和会话运行时验收。"""

import copy
import json
import sqlite3
from contextlib import ExitStack
from dataclasses import replace

import pytest
from test_chat_session import ScriptedModel, response

from patchpilot.core.budget import AgentBudget
from patchpilot.core.compression import fingerprint
from patchpilot.core.memory import (
    GLOBAL_SCOPE,
    MemoryCursor,
    MemoryOptions,
    MemorySnapshot,
    MemorySource,
    MemoryUpdate,
    project_scope,
)
from patchpilot.infrastructure.persistence.memory_store import SQLiteMemoryStore
from patchpilot.model.protocol import ModelFailure
from patchpilot.session_runtime.memory import EXTRACTION_PROMPT, MemoryCoordinator
from patchpilot.session_runtime.session import AgentSession


@pytest.fixture
def database(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    yield store
    store.close()


@pytest.fixture
def sessions(tmp_path, database):
    with ExitStack() as stack:

        def create(model, *, repository="repo", enabled=True, **options):
            repo = tmp_path / repository
            repo.mkdir(exist_ok=True)
            session = AgentSession(
                repo,
                model,
                sessions_root=tmp_path / "sessions",
                memory_backend=database if enabled else None,
                **options,
            )
            stack.callback(session.close)
            return session

        yield create


def source(text="明确陈述", *, kind="user_stated", outcome="unknown"):
    return MemorySource("测试会话", "m0", 0, 0, len(text), fingerprint([text]), text, kind, outcome)


def update(scope, text="使用上下文压缩 ContextBuilder", **options):
    return MemoryUpdate(scope, "project_fact", "上下文压缩", text, text, (source(),), **options)


def extracted(*, kind="preference", scope="global", sources=("s0",), **options):
    operation = {
        "scope": scope,
        "kind": kind,
        "topic": "回复语言",
        "summary": "用户要求中文回复",
        "body": "用户明确要求所有解释使用中文。",
        "source_ids": list(sources),
        "id": None,
        "expected_version": None,
        "outcome": "unknown",
        **options,
    }
    return response(json.dumps({"operations": [operation]}, ensure_ascii=False))


def empty_extraction(arguments):
    assert arguments["system"] == EXTRACTION_PROMPT
    assert arguments["tools"] == []
    return response('{"operations": []}')


def test_store_search_chinese_identifiers_and_scope(database, tmp_path):
    first = project_scope(tmp_path / "first")
    second = project_scope(tmp_path / "second")
    record = database.remember(first, update(first))
    database.remember(second, update(second, "另一个项目的压缩实现"))
    global_record = database.remember(
        first,
        MemoryUpdate(
            GLOBAL_SCOPE, "preference", "语言", "中文解释", "中文解释", (source(kind="manual"),)
        ),
    )
    for query in ("压缩", "ContextBuilder", "context_builder", '压缩" OR scope:global', "压"):
        found = database.search(first, query)
        assert record.id in {r.id for r in found}
        assert all(r.scope in (first, GLOBAL_SCOPE) for r in found)
    assert database.read(second, global_record.id) == global_record
    with pytest.raises(ValueError, match="可访问"):
        database.read(second, record.id)
    assert database.search(first, "不存在的词语") == []


def test_atomic_batch_versions_tombstones_and_empty_replay(database, tmp_path):
    scope = project_scope(tmp_path)
    start = database.initialize_cursor(scope, "会话", MemoryCursor(0, digest=fingerprint([])))
    end = MemoryCursor(1, digest="处理后摘要")
    assert database.apply_batch(scope, "会话", "批次1", start, end, [update(scope)])
    record = database.search(scope, "压缩")[0]
    assert not database.apply_batch(scope, "会话", "批次1", start, end, [update(scope)])
    assert database.read(scope, record.id).version == 1
    conflicting = replace(update(scope), id=record.id, expected_version=9)
    with pytest.raises(ValueError, match="版本冲突"):
        database.apply_batch(
            scope,
            "会话",
            "批次2",
            end,
            replace(end, message=2),
            [
                replace(update(scope), topic="新增主题"),
                conflicting,
            ],
        )
    assert database.count(scope) == 1
    assert database.cursor(scope, "会话") == end
    changed = replace(update(scope, "改用新的压缩策略"), id=record.id, expected_version=1)
    after = replace(end, message=2)
    database.apply_batch(scope, "会话", "批次2", end, after, [changed])
    assert database.read(scope, record.id).version == 2
    assert database.db.execute("SELECT count(*) FROM revisions").fetchone()[0] == 2
    database.forget(scope, record.id)
    database.apply_batch(
        scope, "会话", "迟到批次", after, replace(after, message=3), [update(scope)]
    )
    assert database.count(scope) == 0
    with pytest.raises(ValueError):
        database.read(scope, record.id)
    revived = database.remember(scope, update(scope))
    assert revived.id == record.id and revived.version == 4
    end = database.cursor(scope, "会话")
    final = replace(end, message=4)
    assert database.apply_batch(scope, "会话", "空批次", end, final, [])
    assert not database.apply_batch(scope, "会话", "空批次", end, final, [])


def test_database_rejects_unknown_schema_and_can_reopen(database, tmp_path):
    database.db.execute("PRAGMA user_version=2")
    with pytest.raises(ValueError, match="版本"):
        SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    database.db.execute("PRAGMA user_version=1")
    with ExitStack() as stack:
        another = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
        stack.callback(another.close)
        assert another.count(project_scope(tmp_path)) == 0


def test_manual_commands_have_no_model_calls_and_reuse_across_sessions(sessions):
    session = sessions(ScriptedModel())
    remembered = json.loads(session.submit("/memory remember --kind decision 采用上下文压缩").text)
    assert remembered["kind"] == "decision" and remembered["manual"]
    preference = json.loads(session.submit("/memory remember --global 使用中文解释").text)
    assert preference["scope"] == GLOBAL_SCOPE
    found = json.loads(session.submit("/memory search 压缩").text)
    assert found[0]["id"] == remembered["id"]
    assert json.loads(session.submit("/memory show " + remembered["id"]).text)["sources"]
    same = sessions(ScriptedModel())
    other = sessions(ScriptedModel(), repository="other")
    assert same.submit("/memory show " + remembered["id"]).status == "status"
    assert other.submit("/memory show " + remembered["id"]).status == "error"
    assert other.submit("/memory show " + preference["id"]).status == "status"
    assert session.submit("/memory remember --global --kind decision 越界").status == "error"
    assert session.submit("/memory forget " + remembered["id"]).status == "status"
    assert same.submit("/memory show " + remembered["id"]).status == "error"
    assert not session.model.requests
    assert session.messages == []


def test_auto_extract_and_next_session_recall(sessions, database):
    first = sessions(ScriptedModel(response("好的"), extracted()))
    assert first.submit("以后请使用中文解释").status == "answered"
    assert first.stats["memory"] == {"calls": 1, "tokens": 8, "unknown_usage": 0}
    record = database.preferences()[0]
    assert record.sources[0].ref == "m0"
    assert record.sources[0].kind == "user_stated"
    assert len(first.messages) == 2
    model = ScriptedModel(response("中文回答"), empty_extraction)
    second = sessions(model, repository="other")
    assert second.submit("解释入口").status == "answered"
    assert "用户要求中文回复" in model.requests[0]["messages"][-1]["content"]
    assert "本次工作区检查" in model.requests[0]["messages"][-1]["content"]
    assert second.messages[-1]["content"][0]["text"] == "中文回答"


@pytest.mark.parametrize(
    "bad",
    [
        response("非法JSON"),
        response('{"operations": []}', stop="max_tokens"),
        extracted(sources=("s99",)),
        extracted(scope="global", kind="decision"),
        extracted(id="不存在", expected_version=1),
        extracted(outcome="success"),
        response("", calls=[("非法工具", "read_file", {"path": "secret"})], stop="end_turn"),
    ],
)
def test_bad_extraction_keeps_answer_cursor_and_retries(sessions, database, bad):
    model = ScriptedModel(response("回答已完成"), bad, empty_extraction)
    session = sessions(model)
    cursor = database.cursor(session.memory.scope, session.session_id)
    result = session.submit("请记住中文回复偏好")
    assert result.status == "answered" and result.text == "回答已完成"
    assert database.cursor(session.memory.scope, session.session_id) == cursor
    assert session.memory.last_error
    assert len(session.messages) == 2
    retried = session.submit("/memory extract")
    assert retried.status == "status"
    assert database.cursor(session.memory.scope, session.session_id).message == 2
    assert session.stats["memory"]["calls"] == 2


def test_extraction_model_failure_accounts_unknown_usage(sessions):
    session = sessions(ScriptedModel(response("正常回答"), ModelFailure("provider_error")))
    assert session.submit("问题").status == "answered"
    assert session.stats["memory"] == {"calls": 1, "tokens": 0, "unknown_usage": 1}
    assert session.memory.last_error


def test_memory_shares_budget_without_changing_finished_answer(sessions):
    model = ScriptedModel(response("最终回答"), empty_extraction)
    session = sessions(model, budget=AgentBudget(max_model_calls=1))
    assert session.submit("请解释").status == "answered"
    assert session.stats["memory"]["calls"] == 0
    assert "预算" in session.memory.last_error
    assert len(model.requests) == 1
    assert session.submit("/memory extract").status == "status"
    assert session.stats["memory"]["calls"] == 1


def test_cancelled_worker_does_not_extract_or_advance(sessions, database):
    session = sessions(ScriptedModel(KeyboardInterrupt()))
    start = database.cursor(session.memory.scope, session.session_id)
    assert session.submit("问题").status == "paused"
    assert session.stats["memory"]["calls"] == 0
    assert database.cursor(session.memory.scope, session.session_id) == start


def test_snapshot_is_stable_read_only_and_shared_with_evaluator(sessions):
    setup = sessions(ScriptedModel())
    setup.submit("/memory remember --global 始终使用中文解释")
    worker = ScriptedModel(response("回答"), empty_extraction)
    evaluator = ScriptedModel(
        response('{"ok": true, "reason": "本次证据满足条件", "impossible": false}')
    )
    session = sessions(worker, evaluator_model=evaluator)
    assert session.submit("/goal 解释模块").status == "achieved"
    snapshot = session.memory.snapshot
    assert snapshot.content in worker.requests[0]["messages"][-1]["content"]
    conversation = json.loads(evaluator.requests[0]["messages"][0]["content"])["conversation"]
    assert conversation[-1] == snapshot.messages()[0]
    returned = snapshot.messages()
    returned[0]["content"] = "外部改写"
    assert snapshot.messages()[0]["content"] != "外部改写"
    before = len(worker.requests)
    session.context.view()
    session.context.fits("系统", [])
    session.context.build("系统", [])
    assert len(worker.requests) == before
    assert session.memory.snapshot is snapshot
    assert session.stats["memory"]["calls"] == 1


def test_snapshot_serialized_budget_and_project_degradation(database, tmp_path):
    scope = project_scope(tmp_path)
    preference = database.remember(
        scope, MemoryUpdate(GLOBAL_SCOPE, "preference", "语言", "中文解释", "中文解释", (source(),))
    )
    records = [database.remember(scope, replace(update(scope), topic=f"主题{i}")) for i in range(5)]
    snapshot = MemorySnapshot.build([preference], records, max_chars=750)
    assert len(json.dumps(snapshot.messages(), ensure_ascii=False)) <= 750
    assert "中文解释" in snapshot.content
    assert "上下文压缩" not in snapshot.content
    assert not MemorySnapshot.build([preference], records, max_chars=10).messages()


def test_long_originals_resume_by_fragment_without_thinking(database, tmp_path):
    messages = []
    coordinator = MemoryCoordinator(
        database, tmp_path, "长会话", messages, MemoryOptions(extraction_chars=2500)
    )
    messages.extend(
        [
            {"role": "user", "content": "上下文压缩" * 1800},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "绝不提取的推理", "signature": "签名"},
                    {"type": "text", "text": "可见答案"},
                ],
            },
        ]
    )
    original = copy.deepcopy(messages)
    requests = []

    def complete(system, payload):
        requests.append(payload)
        assert len(system) + len(json.dumps(payload, ensure_ascii=False)) + 2 <= 2500
        return response('{"operations": []}')

    for _ in range(60):
        result = coordinator.extract(
            messages, max_chars=2500, complete=complete, check=lambda: None
        )
        if not result["pending_messages"]:
            break
        coordinator = MemoryCoordinator(
            database, tmp_path, "长会话", messages, MemoryOptions(extraction_chars=2500)
        )
    else:
        pytest.fail("长原文水位未能推进至末尾")
    assert len(requests) > 1 and messages == original
    assert "绝不提取的推理" not in json.dumps(requests, ensure_ascii=False)
    assert database.cursor(coordinator.scope, "长会话").message == 2
    messages[0]["content"] = "原文被更改"
    with pytest.raises(ValueError, match="引用已变化"):
        coordinator.status(messages)


def test_failure_tool_cannot_create_success_or_global_preference(database, tmp_path):
    messages = []
    memory = MemoryCoordinator(database, tmp_path, "证据会话", messages)
    messages.extend(
        [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "命令", "name": "run_command", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "命令",
                        "content": '{"ok": true, "exit_code": 1, "output": "检查失败"}',
                    }
                ],
            },
        ]
    )
    for candidate in (
        extracted(),
        extracted(kind="experience", scope="project", outcome="success"),
    ):
        with pytest.raises(ValueError):
            memory.extract(
                messages, max_chars=120000, complete=lambda *_, r=candidate: r, check=lambda: None
            )
    assert database.count(memory.scope) == 0
    assert database.cursor(memory.scope, "证据会话").message == 0


def test_current_memory_tools_are_read_only_scoped_and_not_recaptured(sessions):
    first = sessions(ScriptedModel())
    record = json.loads(first.submit("/memory remember 原生上下文压缩").text)
    other = sessions(ScriptedModel(), repository="other")
    with pytest.raises(ValueError):
        other.tool_provider.dispatch("memory_read", {"id": record["id"]}, remaining=1)
    assert first.tool_provider.dispatch("memory_search", {"query": "压缩"}, remaining=1)["ok"]
    assert first.tool_provider.dispatch("memory_read", {"id": record["id"]}, remaining=1)[
        "historical"
    ]
    disabled = sessions(ScriptedModel(), enabled=False)
    assert not any(t["name"].startswith("memory_") for t in disabled.tool_provider.definitions())
    assert disabled.submit("/memory remember 内容").status == "error"


def test_old_session_enable_skips_backfill_and_enabled_resume_keeps_pending(
    sessions, database, tmp_path
):
    old = sessions(ScriptedModel(response("旧回答")), enabled=False)
    old.submit("旧内容不应回填")
    old.close()
    restored = sessions(
        ScriptedModel(response("新回答"), response("不是JSON")), session_id=old.session_id
    )
    assert database.cursor(restored.memory.scope, restored.session_id).message == 2
    restored.submit("新内容")
    assert database.cursor(restored.memory.scope, restored.session_id).message == 2
    restored.close()
    again = sessions(ScriptedModel(empty_extraction), session_id=old.session_id)
    assert again.submit("/memory extract").status == "status"
    assert database.cursor(again.memory.scope, again.session_id).message == 4
    saved = json.loads((again.store.directory / "state.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1 and saved["stats"]["memory"]["calls"] == 2


def test_database_lock_is_contained_and_answer_survives(sessions, database):
    session = sessions(ScriptedModel(response("正常完成"), empty_extraction, empty_extraction))
    connection = sqlite3.connect(database.location, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        assert session.submit("解释实现").status == "answered"
        assert "locked" in session.memory.last_error
        assert session.submit("/memory remember 记录").status == "error"
    finally:
        connection.execute("ROLLBACK")
        connection.close()
    assert session.submit("/memory extract").status == "status"
    assert database.cursor(session.memory.scope, session.session_id).message == 2
    assert session.stats["memory"]["calls"] == 2


def test_preference_update_keeps_id_and_versions(sessions, database):
    model = ScriptedModel(response("收到"), extracted(), response("已调整"))
    session = sessions(model)
    session.submit("以后用中文解释")
    before = database.preferences()[0]
    model.turns = iter(
        [
            response("已调整"),
            extracted(
                id=before.id,
                expected_version=1,
                summary="用户改为英文解释",
                body="用户改为英文解释。",
            ),
        ]
    )
    assert session.submit("以后请改用英文解释").status == "answered"
    after = database.preferences()[0]
    assert after.id == before.id and after.version == 2
    assert after.sources[0].ref == "m2"
    assert "英文" in after.body
    assert (
        database.db.execute("SELECT count(*) FROM revisions WHERE id=?", (after.id,)).fetchone()[0]
        == 2
    )


def test_success_experience_retains_historical_tool_evidence(database, tmp_path):
    messages = []
    memory = MemoryCoordinator(database, tmp_path, "成功会话", messages)
    messages.extend(
        [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "命令", "name": "run_command", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "命令",
                        "content": '{"ok": true, "exit_code": 0, "output": "检查通过"}',
                    }
                ],
            },
        ]
    )
    memory.extract(
        messages,
        max_chars=120000,
        check=lambda: None,
        complete=lambda *_: extracted(
            kind="experience",
            scope="project",
            outcome="success",
            topic="检查命令",
            summary="当时检查通过",
            body="该命令在记录时退出码为零。",
        ),
    )
    record = database.search(memory.scope, "检查")[0]
    assert record.outcome == "success"
    assert record.sources[0].kind == "observed_at_time"
    assert record.sources[0].outcome == "success" and record.sources[0].ref == "m1"


def test_read_tool_paginates_full_body_and_validates_arguments(sessions, database):
    session = sessions(ScriptedModel())
    body = "正文" * 3000
    record = database.remember(
        session.memory.scope, replace(update(session.memory.scope, "正文摘要"), body=body)
    )
    provider = session.tool_provider
    first = provider.dispatch("memory_read", {"id": record.id}, remaining=1)["memory"]
    second = provider.dispatch(
        "memory_read", {"id": record.id, "offset": first["next_offset"]}, remaining=1
    )["memory"]
    assert first["body"] + second["body"] == body
    assert second["next_offset"] is None
    for invalid in (
        {"id": record.id, "offset": -1},
        {"id": record.id, "limit": True},
        {"id": record.id, "scope": "别的项目"},
    ):
        with pytest.raises(ValueError):
            provider.dispatch("memory_read", invalid, remaining=1)


def test_memory_tool_results_are_not_new_extraction_evidence(sessions):
    setup = sessions(ScriptedModel())
    record = json.loads(setup.submit("/memory remember 独特历史经验甲乙丙").text)

    def check_sources(arguments):
        payload = json.loads(arguments["messages"][0]["content"])
        assert all("独特历史经验甲乙丙" not in s["excerpt"] for s in payload["sources"])
        return response('{"operations": []}')

    model = ScriptedModel(
        response(calls=[("查记忆", "memory_read", {"id": record["id"]})]),
        response("检索完成"),
        check_sources,
    )
    session = sessions(model)
    assert session.submit("查看以前记录").status == "answered"
    assert session.stats["memory"]["calls"] == 1


def test_cancelled_extraction_keeps_answer_and_watermark(sessions, database):
    session = sessions(ScriptedModel(response("已完成回答"), KeyboardInterrupt()))
    assert session.submit("请解释").status == "answered"
    assert session.control.stopped
    assert database.cursor(session.memory.scope, session.session_id).message == 0
    calls = session.stats["memory"]["calls"]
    session.close()
    assert session.stats["memory"]["calls"] == calls


def test_extraction_redacts_credentials_and_rejects_assistant_preferences(database, tmp_path):
    messages = []
    memory = MemoryCoordinator(database, tmp_path, "凭据会话", messages)
    messages.extend(
        [
            {"role": "user", "content": "ANTHROPIC_API_KEY=example-secret-value"},
            {"role": "assistant", "content": [{"type": "text", "text": "我推测用户偏好中文"}]},
        ]
    )

    def complete(system, payload):
        text = json.dumps(payload, ensure_ascii=False)
        assert "example-secret-value" not in text
        return extracted(sources=("s1",))

    with pytest.raises(ValueError, match="明确陈述"):
        memory.extract(messages, max_chars=120000, complete=complete, check=lambda: None)
    assert database.count(memory.scope) == 0


def test_memory_backend_failure_does_not_prevent_conversation(tmp_path):
    class BrokenBackend:
        location = "不可用数据库"

        def initialize_cursor(self, *args):
            raise sqlite3.OperationalError("数据库不可用")

    session = AgentSession(
        tmp_path,
        ScriptedModel(response("正常回答")),
        sessions_root=tmp_path / "sessions",
        memory_backend=BrokenBackend(),
    )
    try:
        assert session.submit("问题").status == "answered"
        assert "数据库不可用" in session.submit("/memory").text
    finally:
        session.close()
