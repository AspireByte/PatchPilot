"""验证多轮上下文、真实文件改动、目标续跑与恢复边界。"""

import copy
import hashlib
import json

import pytest
from anthropic.types import Message

from patchpilot.agent import AgentBudget, ModelFailure
from patchpilot.session import AgentSession


def response(text="回答", *, calls=(), stop=None, tokens=4):
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": identifier, "name": name, "input": arguments}
        for identifier, name, arguments in calls
    ]
    return Message.model_validate(
        {
            "id": "msg-test",
            "type": "message",
            "role": "assistant",
            "model": "测试模型",
            "content": content,
            "stop_reason": stop or ("tool_use" if calls else "end_turn"),
            "usage": {"input_tokens": tokens, "output_tokens": tokens},
        }
    )


class ScriptedModel:
    kind = "测试替身"

    def __init__(self, *turns):
        self.turns = iter(turns)
        self.requests = []

    def complete(self, **arguments):
        self.requests.append(copy.deepcopy(arguments))
        turn = next(self.turns)
        if isinstance(turn, BaseException):
            raise turn
        return turn(arguments) if callable(turn) else turn


def make_session(tmp_path, model, **options):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return AgentSession(repo, model, sessions_root=tmp_path / "sessions", **options)


def test_three_turns_keep_context_and_do_not_evaluate(tmp_path):
    model = ScriptedModel(response("第一轮"), response("第二轮"), response("第三轮"))
    evaluator = ScriptedModel()
    session = make_session(tmp_path, model, evaluator_model=evaluator)
    try:
        for query in ("记住名称小明", "刚才叫什么", "继续解释"):
            assert session.submit(query).status == "answered"
        assert "小明" in model.requests[-1]["messages"][0]["content"]
        assert len(model.requests[-1]["messages"]) == 5
        assert not evaluator.requests
    finally:
        session.close()


def test_read_edit_followup_changes_same_local_repo(tmp_path):
    def edit(arguments):
        observation = json.loads(arguments["messages"][-1]["content"][0]["content"])
        return response(
            "修改中",
            calls=[
                (
                    "edit",
                    "edit_file",
                    {
                        "path": "main.py",
                        "expected_hash": observation["sha256"],
                        "old_text": "value = 1",
                        "new_text": "value = 2",
                    },
                )
            ],
        )

    model = ScriptedModel(
        response(calls=[("read", "read_file", {"path": "main.py"})]),
        edit,
        response("已修改"),
        response("刚才把1改成2"),
    )
    session = make_session(tmp_path, model)
    path = session.tools.repo / "main.py"
    path.write_text("# 用户已有内容\nvalue = 1\n", encoding="utf-8")
    try:
        assert session.submit("改为2").status == "answered"
        assert path.read_text(encoding="utf-8") == "# 用户已有内容\nvalue = 2\n"
        assert session.submit("解释刚才修改").text == "刚才把1改成2"
        assert "value = 1" in session.tools.get_diff()["diff"]
    finally:
        session.close()


def test_goal_continues_then_achieves_and_shares_accounting(tmp_path):
    worker = ScriptedModel(response("还没完成"), response("已补齐证据"))
    evaluator = ScriptedModel(
        response(json.dumps({"ok": False, "reason": "缺少检查", "impossible": False})),
        response(json.dumps({"ok": True, "reason": "证据充分", "impossible": False})),
    )
    session = make_session(tmp_path, worker, evaluator_model=evaluator)
    try:
        assert session.submit("/goal 完成检查").status == "achieved"
        assert session.turn_calls == 4
        assert session.stats["worker"]["tokens"] == 16
        assert session.stats["evaluator"]["tokens"] == 16
        assert evaluator.requests[0]["tools"] == []
        assert "缺少检查" in worker.requests[-1]["messages"][-1]["content"]
    finally:
        session.close()


@pytest.mark.parametrize(
    "evaluation",
    [
        "不是JSON",
        '{"ok": "true", "reason": "完成", "impossible": false}',
        '{"ok": true, "reason": "", "impossible": false}',
        '{"ok": true, "reason": "矛盾", "impossible": true}',
    ],
)
def test_invalid_evaluator_pauses_instead_of_looping(tmp_path, evaluation):
    worker = ScriptedModel(response())
    evaluator = ScriptedModel(response(evaluation))
    session = make_session(tmp_path, worker, evaluator_model=evaluator)
    try:
        assert session.submit("/goal 运行检查").status == "paused"
        assert session.goal.condition == "运行检查"
        assert session.goal.status == "paused"
        assert len(worker.requests) == 1
    finally:
        session.close()


def test_evaluator_cannot_exceed_shared_call_budget(tmp_path):
    session = make_session(
        tmp_path,
        ScriptedModel(response()),
        evaluator_model=ScriptedModel(),
        budget=AgentBudget(max_model_calls=1),
    )
    try:
        result = session.submit("/goal 检查")
        assert result.status == "paused"
        assert "模型调用预算" in result.reason
        assert session.stats["evaluator"]["calls"] == 0
    finally:
        session.close()


def test_tool_budget_preserves_all_result_pairs(tmp_path):
    worker = ScriptedModel(
        response(
            calls=[
                ("one", "list_files", {}),
                ("two", "list_files", {}),
            ]
        )
    )
    session = make_session(tmp_path, worker, budget=AgentBudget(max_tool_calls=1))
    try:
        assert session.submit("查看").status == "paused"
        results = session.messages[-1]["content"]
        assert [item["tool_use_id"] for item in results] == ["one", "two"]
        assert not results[0]["is_error"] and results[1]["is_error"]
    finally:
        session.close()


def test_restore_keeps_history_goal_diff_and_requires_fresh_read(tmp_path):
    session = make_session(tmp_path, ScriptedModel())
    file = session.tools.repo / "main.py"
    file.write_text("旧内容", encoding="utf-8")
    read = session.tools.dispatch("read_file", {"path": "main.py"})
    assert session.tools.dispatch(
        "write_file",
        {
            "path": "main.py",
            "content": "新内容",
            "expected_hash": read["sha256"],
        },
    )["ok"]
    session.goal.set("待完成目标")
    session.messages.append({"role": "user", "content": "已有对话"})
    session_id = session.session_id
    repo = session.tools.repo
    session.close()
    restored = AgentSession(
        None, ScriptedModel(), sessions_root=tmp_path / "sessions", session_id=session_id
    )
    try:
        assert restored.tools.repo == repo
        assert restored.goal.status == "paused"
        assert restored.messages[0]["content"] == "已有对话"
        assert "旧内容" in restored.tools.get_diff()["diff"]
        assert not restored.tools.observed
        read_hash = hashlib.sha256(file.read_bytes()).hexdigest()
        result = restored.tools.dispatch(
            "write_file",
            {
                "path": "main.py",
                "content": "覆盖",
                "expected_hash": read_hash,
            },
        )
        assert not result["ok"]
        assert file.read_text(encoding="utf-8") == "新内容"
    finally:
        restored.close()


def test_crash_checkpoint_does_not_replay_command(tmp_path):
    session = make_session(tmp_path, ScriptedModel(), allow_commands=True)
    session.messages.append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "pending",
                    "name": "run_command",
                    "input": {"command": "不应执行"},
                }
            ],
        }
    )
    session_id = session.session_id
    session.close()
    restored = AgentSession(
        None, ScriptedModel(response()), sessions_root=tmp_path / "sessions", session_id=session_id
    )
    try:
        result = restored.messages[-1]["content"][0]
        assert result["tool_use_id"] == "pending" and result["is_error"]
        assert "未自动重放" in result["content"]
        assert restored.submit("继续讨论").status == "answered"
        assert restored.stats["tool_calls"] == 0
    finally:
        restored.close()


def test_exclusive_lock_and_repository_mismatch(tmp_path):
    session = make_session(tmp_path, ScriptedModel())
    session_id = session.session_id
    try:
        with pytest.raises(ValueError, match="另一个进程"):
            AgentSession(
                None, ScriptedModel(), sessions_root=tmp_path / "sessions", session_id=session_id
            )
    finally:
        session.close()
    with pytest.raises(ValueError, match="仓库不一致"):
        AgentSession(
            tmp_path, ScriptedModel(), sessions_root=tmp_path / "sessions", session_id=session_id
        )


def test_interrupt_and_provider_failure_preserve_session(tmp_path):
    worker = ScriptedModel(KeyboardInterrupt(), ModelFailure("provider_error"), response("可继续"))
    session = make_session(tmp_path, worker)
    try:
        assert session.submit("/goal 检查").status == "paused"
        assert session.submit("继续讨论").reason == "provider_error"
        assert session.submit("现在呢").text == "可继续"
        assert session.stats["worker"]["unknown_usage"] == 2
        assert session.goal.status == "paused"
    finally:
        session.close()


def test_context_limit_makes_no_request(tmp_path):
    worker = ScriptedModel()
    session = make_session(tmp_path, worker, budget=AgentBudget(max_context_chars=1))
    try:
        result = session.submit("问题")
        assert result.status == "paused" and "/new" in result.reason
        assert not worker.requests
    finally:
        session.close()


def test_corrupt_saved_state_is_rejected_without_model_call(tmp_path):
    session = make_session(tmp_path, ScriptedModel())
    session_id = session.session_id
    state_path = session.store.directory / "state.json"
    session.close()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["stats"]["worker"]["calls"] = "不是数字"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    worker = ScriptedModel()
    with pytest.raises(ValueError, match="字段损坏"):
        AgentSession(None, worker, sessions_root=tmp_path / "sessions", session_id=session_id)
    assert not worker.requests


def test_goal_pause_resume_clear_and_impossible(tmp_path):
    evaluator = ScriptedModel(
        response(
            json.dumps(
                {
                    "ok": False,
                    "reason": "需要用户提供验收标准",
                    "impossible": True,
                }
            )
        )
    )
    worker = ScriptedModel(response("请明确要求"), response("普通回答"))
    session = make_session(tmp_path, worker, evaluator_model=evaluator)
    try:
        result = session.submit("/goal 让代码完美")
        assert result.status == "paused" and "验收标准" in result.reason
        assert session.submit("普通问题").status == "answered"
        assert len(evaluator.requests) == 1
        session.submit("/goal clear")
        assert session.goal.status == "none" and not session.goal.condition
        assert session.submit("/goal resume").status == "error"
        assert len(session.messages) == 4
    finally:
        session.close()


def test_continuation_cap_and_explicit_resume(tmp_path):
    unfinished = response(json.dumps({"ok": False, "reason": "证据缺失", "impossible": False}))
    done = response(json.dumps({"ok": True, "reason": "已补齐", "impossible": False}))
    worker = ScriptedModel(response(), response(), response())
    evaluator = ScriptedModel(unfinished, unfinished, done)
    session = make_session(tmp_path, worker, evaluator_model=evaluator, max_continuations=1)
    try:
        assert session.submit("/goal 检查").status == "paused"
        assert "续跑" in session.goal.reason
        assert len(worker.requests) == 2
        assert session.submit("/goal resume").status == "achieved"
        assert session.goal.evaluations == 3
    finally:
        session.close()


def test_token_budget_does_not_start_evaluator(tmp_path):
    worker = ScriptedModel(response(tokens=10))
    evaluator = ScriptedModel()
    session = make_session(
        tmp_path, worker, evaluator_model=evaluator, budget=AgentBudget(max_total_tokens=10)
    )
    try:
        assert session.submit("/goal 检查").status == "paused"
        assert not evaluator.requests
        assert session.turn_tokens == 20
    finally:
        session.close()


def test_deadline_stops_before_tools(tmp_path, monkeypatch):
    clock = [0]

    def slow_response(arguments):
        clock[0] = 601
        return response(calls=[("list", "list_files", {})])

    worker = ScriptedModel(slow_response)
    session = make_session(tmp_path, worker)
    monkeypatch.setattr("patchpilot.session.monotonic", lambda: clock[0])
    try:
        assert session.submit("查看").status == "paused"
        assert session.stats["tool_calls"] == 0
    finally:
        session.close()


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "end_turn"])
def test_invalid_or_incomplete_tool_response_never_executes(tmp_path, stop):
    worker = ScriptedModel(
        response(
            calls=[
                (
                    "create",
                    "write_file",
                    {
                        "path": "should-not-exist.py",
                        "content": "bad",
                        "expected_hash": None,
                    },
                )
            ],
            stop=stop,
        )
    )
    session = make_session(tmp_path, worker)
    try:
        assert session.submit("执行").status == "paused"
        assert not (session.tools.repo / "should-not-exist.py").exists()
        assert session.messages[-1]["content"][0]["is_error"]
    finally:
        session.close()
