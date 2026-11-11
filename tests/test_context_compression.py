"""压缩视图、完整原文、预算与跨会话恢复的行为验证。"""

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_chat_session import ScriptedModel, make_session, response

from patchpilot.core.budget import AgentBudget
from patchpilot.core.compression import HistoryCompactor, serialized
from patchpilot.core.context import ContextBuilder, ContextLimitReached
from patchpilot.service.api import create_app
from patchpilot.service.config import ServiceSettings
from patchpilot.session_runtime.compression import SUMMARY_PROMPT, summarize_history
from patchpilot.session_runtime.session import AgentSession, TurnStopped


def history(turns=8, width=1200):
    return [
        {"role": role, "content": f"第{index}轮 {role} " + "数据" * width}
        for index in range(turns)
        for role in ("user", "assistant")
    ]


def summary_response(arguments):
    assert arguments["system"] == SUMMARY_PROMPT
    assert not arguments["tools"]
    return response('{"summary": "已修改 main.py，测试通过；后续需检查缓存。"}')


def test_read_only_fits_does_not_summarize_and_build_reserves_fixed_content():
    calls = []
    raw = history()
    original = copy.deepcopy(raw)
    compactor = HistoryCompactor(lambda text, limit: calls.append(text) or "简短历史")
    builder = ContextBuilder(raw, 25000, compactor=compactor)
    assert not builder.fits("系统" * 1000, [])
    assert not calls
    _, view, _ = builder.build("系统" * 1000, [])
    assert calls and view != raw and raw == original
    assert len(serialized(view)) + 2000 + 2 <= 25000
    assert view[-6:] == raw[-6:]
    count = len(calls)
    assert builder.fits("系统" * 1000, [])
    assert len(calls) == count


def test_tiers_merge_search_and_decompress_without_losing_originals():
    raw = history(20)
    compactor = HistoryCompactor(lambda text, limit: "历史事实")
    compactor.compact(raw, max_chars=18000)
    assert any(block.tier >= 2 for block in compactor.blocks)
    assert len(compactor._active(compactor.blocks)) < 4
    block = compactor.blocks[0]
    restored = compactor.decompress(block.identifier, raw)
    assert restored == raw[block.start : block.end]
    restored[0]["content"] = "外部修改"
    assert raw[0]["content"] != "外部修改"
    assert compactor.search("第0轮", raw)[0]["ref"] == "m0"
    assert not compactor.search("", raw)
    with pytest.raises(KeyError):
        compactor.decompress("不存在", raw)


def test_tool_pairs_and_thinking_remain_whole():
    raw = [
        {"role": "user", "content": "读取日志"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "推理", "signature": "签名原样"},
                {"type": "tool_use", "id": "a", "name": "read_file", "input": {}},
                {"type": "tool_use", "id": "b", "name": "read_file", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "日志" * 10000},
                {"type": "tool_result", "tool_use_id": "b", "content": "另一个日志"},
            ],
        },
        {"role": "assistant", "content": "读取完成"},
    ] + history(4, 10)
    seen = []
    compactor = HistoryCompactor(lambda text, limit: seen.append(text) or "两个文件读取完成")
    original = copy.deepcopy(raw)
    view = compactor.compact(raw, max_chars=5000)
    assert raw == original
    assert not any("tool_use" in serialized(m) or "tool_result" in serialized(m) for m in view)
    assert "日志" in seen[0]
    assert compactor.blocks[0].end == 4
    assert view[-6:] == raw[-6:]


def test_current_user_turn_and_unfinished_tools_are_never_folded():
    raw = history(4, 20) + [
        {"role": "user", "content": "当前请求"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "open", "name": "x", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "open", "content": "巨量" * 5000}],
        },
    ]
    compactor = HistoryCompactor(lambda *_: "摘要")
    with pytest.raises(ContextLimitReached, match="仍超过"):
        compactor.compact(raw, max_chars=500)
    assert not compactor.blocks


@pytest.mark.parametrize("bad", ["", "x" * 10000, None])
def test_invalid_summary_rolls_back(bad):
    raw = history()
    original = copy.deepcopy(raw)
    compactor = HistoryCompactor(lambda *_: bad)
    with pytest.raises(ContextLimitReached):
        compactor.compact(raw, max_chars=18000)
    assert compactor.view(raw) == original
    assert not compactor.blocks


def test_later_failure_keeps_previously_committed_blocks():
    raw = history()
    compactor = HistoryCompactor(lambda *_: "摘要")
    compactor.compact(raw, max_chars=24000)
    state = copy.deepcopy(compactor.state())
    raw += history(8)

    def fail(*_):
        raise RuntimeError("模型不可用")

    compactor.summarize = fail
    with pytest.raises(ContextLimitReached, match="模型不可用"):
        compactor.compact(raw, max_chars=24000)
    assert compactor.state() == state


def test_summary_requests_are_chunked_and_bounded():
    calls = []

    def complete(system, messages):
        assert len(system) + len(json.dumps(messages, ensure_ascii=False)) + 2 <= 1800
        calls.append(json.loads(messages[0]["content"]))
        return response('{"summary": "已保留事实"}')

    source = '\\"\n' * 2500
    assert summarize_history(source, 100, max_chars=1800, complete=complete) == "已保留事实"
    assert len(calls) > 1
    assert "".join(item["historical_fragment"] for item in calls) == source
    assert calls[1]["previous_summary"] == "已保留事实"


@pytest.mark.parametrize("bad", ["非JSON", "[]", '{"summary": 1}', '{"summary": ""}'])
def test_summary_protocol_rejects_invalid_response(bad):
    with pytest.raises(ValueError):
        summarize_history("历史", 100, max_chars=2000, complete=lambda *_: response(bad))


def test_manual_command_and_resume_preserve_raw_history(tmp_path):
    model = ScriptedModel(*([summary_response] * 24))
    session = make_session(tmp_path, model)
    raw = history()
    session.messages.extend(copy.deepcopy(raw))
    assert session.submit("/compact").status == "status"
    expected = session.context.view()
    identifier = session.session_id
    assert session.messages == raw
    assert session.stats["compactor"]["calls"] > 0
    assert session.stats["worker"]["calls"] == 0
    session.close()
    restored = AgentSession(
        None, ScriptedModel(), sessions_root=tmp_path / "sessions", session_id=identifier
    )
    try:
        assert restored.messages == raw
        assert restored.context.view() == expected
        assert restored.compactor.decompress("b1", raw)
    finally:
        restored.close()


def test_worker_compression_counts_shared_budget(tmp_path):
    model = ScriptedModel(*([summary_response] * 16))
    session = make_session(
        tmp_path, model, budget=AgentBudget(max_context_chars=24000, max_model_calls=1)
    )
    session.messages.extend(history())
    try:
        result = session.submit("继续工作")
        assert result.status == "paused"
        assert len(model.requests) == session.turn_calls == 1
        assert session.stats["compactor"]["calls"] == 1
        assert session.stats["compactor"]["tokens"] == 8
        assert session.stats["worker"]["calls"] == 0
        assert "预算" in result.reason
    finally:
        session.close()


def test_worker_and_evaluator_use_compressed_view(tmp_path):
    def route(arguments):
        if arguments["system"] == SUMMARY_PROMPT:
            return summary_response(arguments)
        assert "历史摘要" in serialized(arguments["messages"])
        assert len(serialized(arguments["messages"])) < len(serialized(session.messages))
        return response("任务已完成")

    worker = ScriptedModel(*([route] * 30))
    evaluator = ScriptedModel(response('{"ok": true, "reason": "证据充分", "impossible": false}'))
    session = make_session(
        tmp_path,
        worker,
        evaluator_model=evaluator,
        budget=AgentBudget(max_context_chars=25000, max_model_calls=30),
    )
    session.messages.extend(history())
    try:
        assert session.submit("/goal 检查工作").status == "achieved"
        conversation = json.loads(evaluator.requests[0]["messages"][0]["content"])["conversation"]
        assert "历史摘要" in serialized(conversation)
        assert conversation[-1]["role"] == "assistant"
        assert session.turn_calls == len(worker.requests) + len(evaluator.requests)
        assert session.turn_tokens == 8 * session.turn_calls
    finally:
        session.close()


def test_old_session_state_without_compression_fields_loads(tmp_path):
    session = make_session(tmp_path, ScriptedModel())
    identifier = session.session_id
    directory = session.store.directory
    session.close()
    path = directory / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["compression"]
    del state["stats"]["compactor"]
    path.write_text(json.dumps(state), encoding="utf-8")
    restored = AgentSession(
        None, ScriptedModel(), sessions_root=tmp_path / "sessions", session_id=identifier
    )
    try:
        assert not restored.compactor.blocks
        assert restored.stats["compactor"]["calls"] == 0
    finally:
        restored.close()


def test_tampered_compression_range_rejected():
    raw = history()
    compactor = HistoryCompactor(lambda *_: "摘要")
    compactor.compact(raw, max_chars=24000)
    state = json.loads(json.dumps(compactor.state()))
    raw[0]["content"] = "已改变"
    with pytest.raises(ValueError, match="不一致"):
        HistoryCompactor(lambda *_: "摘要").restore(state, raw)


def test_repeated_compaction_and_merged_state_resume():
    raw = history(20)
    compactor = HistoryCompactor(lambda *_: "事实摘要")
    compactor.compact(raw, max_chars=18000)
    assert any(b.tier == 3 for b in compactor.blocks)
    raw += history(8)
    compactor.compact(raw, max_chars=18000)
    restored = HistoryCompactor(lambda *_: "事实摘要")
    restored.restore(json.loads(json.dumps(compactor.state())), raw)
    assert restored.view(raw) == compactor.view(raw)
    for block in restored.blocks:
        assert restored.decompress(block.identifier, raw) == raw[block.start : block.end]
    damaged = json.loads(json.dumps(compactor.state()))
    parent = next(b for b in damaged["blocks"] if b["children"])
    parent["children"] = ["不存在"]
    with pytest.raises(ValueError, match="引用损坏"):
        HistoryCompactor(lambda *_: "事实摘要").restore(damaged, raw)


def test_recent_thinking_and_tool_pairs_are_byte_preserved():
    recent = [
        {"role": "user", "content": "现在的工作"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "思考", "signature": "opaque-signature"},
                {"type": "redacted_thinking", "data": "opaque-data"},
                {"type": "tool_use", "id": "recent", "name": "read", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "recent", "content": "结果"}],
        },
    ]
    raw = history(12) + recent
    compactor = HistoryCompactor(lambda *_: "事实摘要")
    view = compactor.compact(raw, max_chars=20000)
    assert serialized(view[-3:]) == serialized(recent)


def test_failed_summary_accounts_unknown_usage_without_modifying_history(tmp_path):
    model = ScriptedModel(RuntimeError("摘要服务不可用"))
    session = make_session(tmp_path, model)
    raw = history()
    session.messages.extend(copy.deepcopy(raw))
    try:
        assert session.submit("/compact").status == "error"
        assert session.messages == raw
        assert not session.compactor.blocks
        assert session.stats["compactor"] == {"calls": 1, "tokens": 0, "unknown_usage": 1}
    finally:
        session.close()


def test_session_concurrent_operation_rejected(tmp_path):
    session = make_session(tmp_path, ScriptedModel())
    try:
        with session._operation_lock, ThreadPoolExecutor(1) as executor:
            with pytest.raises(TurnStopped, match="正在执行"):
                executor.submit(session.compact).result()
    finally:
        session.close()


def test_http_compact_requires_auth_and_explicit_session(tmp_path):
    session = make_session(tmp_path, ScriptedModel(*([summary_response] * 24)))
    session.messages.extend(history())
    settings = ServiceSettings(
        database_url="postgresql+psycopg://unused",
        api_token="test-token-123456",
        data_root=tmp_path / "data",
        tasks_root=tmp_path / "tasks",
    )

    class UnusedRepository:
        pass

    app = create_app(settings, repository=UnusedRepository(), session=session)
    try:
        client = TestClient(app)
        url = f"/v1/sessions/{session.session_id}/compact"
        assert client.post(url).status_code == 401
        result = client.post(url, headers={"Authorization": "Bearer test-token-123456"})
        assert result.status_code == 200
        assert result.json()["saved_chars"] > 0
        assert (
            client.post(
                "/v1/sessions/00000000-0000-0000-0000-000000000000/compact",
                headers={"Authorization": "Bearer test-token-123456"},
            ).status_code
            == 404
        )
    finally:
        session.close()
