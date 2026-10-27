"""脚本模型配合真实文件工具和真实宿主 Harness 的会话验证。"""

import json

from test_chat_session import ScriptedModel, response

from patchpilot.harness import HarnessService
from patchpilot.session import AgentSession


def test_edit_check_goal_followup_and_resume_share_evidence(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".patchpilot").mkdir(parents=True)
    (repo / ".patchpilot/harness.toml").write_text(
        'runtime="host"\n[profiles.test]\nargv=["python", "check.py"]', encoding="utf-8"
    )
    source = repo / "main.py"
    source.write_text("# 用户已有内容\nvalue = 1\n", encoding="utf-8")
    (repo / "check.py").write_text(
        "from pathlib import Path\nassert 'value = 2' in Path('main.py').read_text(encoding='utf-8')\n"
        "print('checked value=2')\n",
        encoding="utf-8",
    )

    def edit(request):
        observed = json.loads(request["messages"][-1]["content"][0]["content"])
        return response(
            calls=[
                (
                    "edit",
                    "edit_file",
                    {
                        "path": "main.py",
                        "expected_hash": observed["sha256"],
                        "old_text": "value = 1",
                        "new_text": "value = 2",
                    },
                )
            ]
        )

    def evaluate(request):
        evidence = json.loads(request["messages"][0]["content"])["conversation"]
        results = [
            json.loads(block["content"])
            for message in evidence
            if message["role"] == "user" and isinstance(message["content"], list)
            for block in message["content"]
        ]
        check = next(item["harness"] for item in results if "harness" in item)
        assert check["status"] == "passed" and "checked value=2" in check["output"]
        assert check["runtime"] == "host" and not check["isolated"]
        return response('{"ok":true,"reason":"已取得执行证据","impossible":false}')

    worker = ScriptedModel(
        response(calls=[("read", "read_file", {"path": "main.py"})]),
        edit,
        response(calls=[("check", "run_harness_check", {"profile": "test"})]),
        response("已修改，宿主检查通过"),
        response("前面检查了value=2"),
    )
    events = []
    session = AgentSession(
        repo,
        worker,
        evaluator_model=ScriptedModel(evaluate),
        sessions_root=tmp_path / "sessions",
        allow_harness=True,
        on_event=events.append,
    )
    try:
        assert session.submit("/goal 修改value并检查").status == "achieved"
        assert session.turn_calls == 5 and session.turn_tools == 3
        assert source.read_text(encoding="utf-8") == "# 用户已有内容\nvalue = 2\n"
        assert session.submit("刚才验证了什么").status == "answered"
        assert len(worker.requests) == 5
        assert len([event for event in events if event["event"] == "harness_finished"]) == 1
        session.goal.set("后续待处理目标")
        identifier = session.session_id
    finally:
        session.close()

    def never_replay(*args, **kwargs):
        raise AssertionError("恢复不应重放 Harness")

    monkeypatch.setattr(HarnessService, "run", never_replay)
    restored = AgentSession(
        None,
        ScriptedModel(response("仍有完整证据")),
        sessions_root=tmp_path / "sessions",
        session_id=identifier,
    )
    try:
        assert restored.goal.status == "paused"
        assert not restored.tools.observed
        assert restored.tools.harness is None
        assert restored.submit("继续解释之前的检查").status == "answered"
        assert "snapshot_sha256" in json.dumps(restored.messages)
        assert "用户已有内容" in restored.tools.get_diff()["diff"]
    finally:
        restored.close()


def test_pending_harness_call_is_marked_interrupted_and_not_replayed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    session = AgentSession(repo, ScriptedModel(), sessions_root=tmp_path / "sessions")
    session.context.append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "pending-check",
                    "name": "run_harness_check",
                    "input": {"profile": "test"},
                }
            ],
        }
    )
    identifier = session.session_id
    session.close()
    monkeypatch.setattr(HarnessService, "run", lambda *args, **kwargs: pytest_fail())
    restored = AgentSession(
        None,
        ScriptedModel(),
        sessions_root=tmp_path / "sessions",
        session_id=identifier,
        allow_harness=True,
    )
    try:
        result = restored.messages[-1]["content"][0]
        assert result["tool_use_id"] == "pending-check" and result["is_error"]
        assert "未自动重放" in result["content"]
    finally:
        restored.close()


def pytest_fail():
    raise AssertionError("不能重放历史检查")


def test_old_session_without_harness_metadata_still_restores(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    session = AgentSession(repo, ScriptedModel(), sessions_root=tmp_path / "sessions")
    identifier = session.session_id
    path = session.store.directory / "state.json"
    session.close()
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved.pop("harness")
    path.write_text(json.dumps(saved), encoding="utf-8")
    restored = AgentSession(
        None, ScriptedModel(), sessions_root=tmp_path / "sessions", session_id=identifier
    )
    restored.close()


def test_interrupt_during_harness_pauses_goal_and_saves_result(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".patchpilot").mkdir(parents=True)
    (repo / ".patchpilot/harness.toml").write_text(
        'runtime="host"\n[profiles.test]\nargv=["python", "-V"]', encoding="utf-8"
    )

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("patchpilot.harness.run_process", interrupt)
    worker = ScriptedModel(response(calls=[("check", "run_harness_check", {"profile": "test"})]))
    session = AgentSession(repo, worker, sessions_root=tmp_path / "sessions", allow_harness=True)
    try:
        assert session.submit("/goal 运行检查").status == "paused"
        result = json.loads(session.messages[-1]["content"][0]["content"])
        assert result["harness"]["cancelled"]
        assert session.goal.status == "paused"
    finally:
        session.close()
