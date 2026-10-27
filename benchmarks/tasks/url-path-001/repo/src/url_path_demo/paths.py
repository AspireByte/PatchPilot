"""从调用者提供的字符串段构造 URL 路径。"""

from urllib.parse import quote


def build_path(segments: list[str]) -> str:
    """逐段编码并使用路径分隔符连接，仅返回字符串。"""
    encoded = []
    for segment in segments:
        encoded.append(quote(segment))
    return "/" + "/".join(encoded)
