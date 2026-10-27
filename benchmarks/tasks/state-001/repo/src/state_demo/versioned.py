"""提供带版本匹配的内存状态更新。"""

from dataclasses import dataclass


@dataclass
class VersionedValue:
    """保存字符串及当前版本；调用者通过更新方法变更状态。"""

    value: str
    version: int = 0

    def update(self, new_value: str, *, expected_version: int) -> bool:
        """版本匹配时更新并返回真，否则保持全部状态并返回假。"""
        if expected_version != self.version:
            return False
        self.value = new_value
        return True
