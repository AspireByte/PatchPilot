"""维护者成功登记、异常传播及重试契约验收。"""

import pytest
from idempotency_demo.gate import RequestGate


def test_empty_string_key_and_none_result():
    gate = RequestGate()
    assert gate.run("", lambda: None) is True
    assert gate.run("", lambda: None) is False


def test_several_failures_before_success():
    gate = RequestGate()
    attempts = []
    error = RuntimeError("暂时失败")

    def callback():
        attempts.append(1)
        if len(attempts) < 3:
            raise error

    for _ in range(2):
        with pytest.raises(RuntimeError) as caught:
            gate.run("retry", callback)
        assert caught.value is error
    assert gate.run("retry", callback) is True
    assert gate.run("retry", callback) is False
    assert len(attempts) == 3


def test_failure_does_not_change_other_completed_keys():
    gate = RequestGate()
    assert gate.run("ok", lambda: None) is True

    def fail():
        raise ValueError("失败")

    with pytest.raises(ValueError):
        gate.run("bad", fail)
    assert gate.run("ok", lambda: None) is False
    assert gate.run("bad", lambda: None) is True


def test_false_callback_result_is_still_success():
    gate = RequestGate()
    assert gate.run("false-result", lambda: False) is True
    assert gate.run("false-result", lambda: None) is False


def test_duplicate_does_not_evaluate_callback():
    gate = RequestGate()
    assert gate.run("complete", lambda: 42) is True

    def must_not_run():
        raise AssertionError("重复请求不应执行回调")

    assert gate.run("complete", must_not_run) is False


def test_string_keys_are_not_coerced():
    gate = RequestGate()
    assert gate.run("01", lambda: None) is True
    assert gate.run("1", lambda: None) is True
    assert gate.run("01", lambda: None) is False
