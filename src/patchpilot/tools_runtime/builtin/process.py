"""????????????"""

from pydantic import Field

from .filesystem import Arguments


class CommandArgs(Arguments):
    command: str = Field(min_length=1, max_length=16000)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)
