"""成功请求去重及实例隔离的正常行为。"""

from idempotency_demo.gate import RequestGate


def test_first_success_calls_callback():
    calls = []
    assert RequestGate().run("one", lambda: calls.append("done")) is True
    assert calls == ["done"]


def test_successful_duplicate_is_skipped():
    gate = RequestGate()
    calls = []
    assert gate.run("one", lambda: calls.append(1)) is True
    assert gate.run("one", lambda: calls.append(2)) is False
    assert calls == [1]


def test_distinct_keys_execute_separately():
    gate = RequestGate()
    calls = []
    assert gate.run("one", lambda: calls.append(1)) is True
    assert gate.run("two", lambda: calls.append(2)) is True
    assert calls == [1, 2]


def test_gate_instances_are_independent():
    first, second = RequestGate(), RequestGate()
    assert first.run("one", lambda: None) is True
    assert second.run("one", lambda: None) is True
