"""模型端口与 Provider 适配器。"""

from .protocol import ModelClient, ModelClientAdapter, ModelFailure

__all__ = ["ModelClient", "ModelClientAdapter", "ModelFailure"]
