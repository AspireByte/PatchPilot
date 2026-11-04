"""?????????????????"""

import hashlib

from pydantic import BaseModel, ConfigDict, Field

MAX_FILE_BYTES = 256 * 1024
MAX_OUTPUT = 16000
SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    "benchmarks",
}
PRIVATE_NAMES = {"credentials", "credentials.json", "id_rsa", "id_ed25519"}


class Arguments(BaseModel):
    # 模型输入是不可信 JSON；strict 拒绝自动类型转换，extra=forbid 拒绝未声明字段。
    # 同一组参数类还生成工具 schema，让模型看到的输入约定与本地校验保持一致。
    model_config = ConfigDict(strict=True, extra="forbid")


class ListArgs(Arguments):
    directory: str = "."
    limit: int = Field(default=100, ge=1, le=200)


class SearchArgs(ListArgs):
    query: str = Field(min_length=1, max_length=200)


class ReadArgs(Arguments):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class WriteArgs(Arguments):
    path: str
    content: str = Field(max_length=MAX_FILE_BYTES)
    expected_hash: str | None


class EditArgs(Arguments):
    path: str
    old_text: str = Field(min_length=1, max_length=MAX_FILE_BYTES)
    new_text: str = Field(max_length=MAX_FILE_BYTES)
    expected_hash: str


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def excluded(part: str) -> bool:
    lower = part.lower()
    return (
        (lower.startswith(".") and lower != ".github")
        or lower in SKIP_DIRS
        or lower in PRIVATE_NAMES
        or lower.endswith((".pem", ".key", ".p12", ".pfx"))
        or lower.endswith(".egg-info")
    )
