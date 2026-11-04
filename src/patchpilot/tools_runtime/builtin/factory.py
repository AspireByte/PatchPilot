"""聊天 Runtime 内置工具集的组合入口。"""

from .local import LocalTools

BuiltinToolSet = LocalTools

__all__ = ["BuiltinToolSet", "LocalTools"]
