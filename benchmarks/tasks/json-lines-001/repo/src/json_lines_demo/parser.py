"""解析内存中的 JSON Lines 文本并返回结构化错误。"""

import json


def parse_json_lines(text: str) -> dict:
    """跳过空白行，首个无效 JSON 返回所在行及此前记录。"""
    records = []
    lines = [line for line in text.splitlines() if line.strip()]
    for number, line in enumerate(lines, start=1):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            return {"records": records, "error": {"line": number, "message": "无效 JSON"}}
    return {"records": records, "error": None}
