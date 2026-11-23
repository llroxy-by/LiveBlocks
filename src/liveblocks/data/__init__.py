"""包内数据文件（卡片字典、ArUco 字典位表、版式定义、点阵字体）。

这些 JSON 是**实现输入**而非测试夹具：卡片字典与版式定义决定生成什么卡片、怎么摆；
ArUco 位表与点阵字体保证渲染不需要任何第三方库。修改它们会改变 fixtures 的字节，
因此改完必须重新生成 fixtures（``liveblocks synth generate --scene all``）并同步 PR。
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

DATA_PACKAGE = "liveblocks.data"


def read_bytes(name: str) -> bytes:
    """读取包内数据文件的原始字节。"""
    return resources.files(DATA_PACKAGE).joinpath(name).read_bytes()


def read_json(name: str) -> Any:
    """读取并解析包内数据文件（UTF-8 JSON）。"""
    return json.loads(read_bytes(name).decode("utf-8"))


def data_files() -> list[str]:
    """列出包内全部 JSON 数据文件名（排序后返回，保证跨平台顺序一致）。"""
    root = resources.files(DATA_PACKAGE)
    return sorted(entry.name for entry in root.iterdir() if entry.name.endswith(".json"))


__all__ = ["DATA_PACKAGE", "data_files", "read_bytes", "read_json"]
