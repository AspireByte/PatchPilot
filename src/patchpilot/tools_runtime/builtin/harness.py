"""Harness ????????"""

from pydantic import Field

from .filesystem import Arguments


class HarnessArgs(Arguments):
    profile: str = Field(default="", min_length=0, max_length=100)
