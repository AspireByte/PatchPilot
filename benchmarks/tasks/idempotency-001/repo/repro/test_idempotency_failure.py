"""用户回调失败后再次提交同一个请求。"""

import pytest

from idempotency_demo.gate import RequestGate


@pytest.mark.parametrize("error_type", [RuntimeError, LookupError])
def test_repro_retry_after_callback_failure(error_type):
    gate = RequestGate()
    attempts = []

    def callback():
        attempts.append("called")
        if len(attempts) == 1:
            raise error_type("自建的临时失败")

    with pytest.raises(error_type):
        gate.run("request-1", callback)
    assert gate.run("request-1", callback) is True
    assert attempts == ["called", "called"]
