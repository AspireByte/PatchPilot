"""仅根据 POSIX 路径段计算目录归属，不访问文件系统。"""

from pathlib import PurePosixPath


def relative_member(root: str, candidate: str) -> str | None:
    """目录本身返回空串，子路径返回相对串，无归属返回 None。"""
    root_path = PurePosixPath(root)
    candidate_path = PurePosixPath(candidate)
    if root_path.anchor != "/" or candidate_path.anchor != "/":
        raise ValueError("必须使用单根绝对 POSIX 路径")
    if ".." in root_path.parts or ".." in candidate_path.parts:
        raise ValueError("路径不得包含父目录段")
    root_text = str(root_path)
    candidate_text = str(candidate_path)
    if not candidate_text.startswith(root_text):
        return None
    return candidate_text[len(root_text):].lstrip("/")
