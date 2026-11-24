"""``docs/UI.md`` 与实现的核对（文档缺口）。

与其他层的 ``*_document_gaps`` 同一思路：**入库文档必须能被机器核对**——面板标题、
``LB-U0xx`` 问题码、``RunHeader`` 字段、分层耗时键、可复制粘贴的命令、以及文档里提到的
路径是否真实存在。段落被删/被改写时这里会报缺口，``tests/test_ui_docs.py`` 还会做
反向验证（先删一行再断言必须报缺口），防止核对函数本身变成永真式。
"""

from __future__ import annotations

import re
from pathlib import Path

from .protocol import (
    ISSUE_CODES,
    ISSUE_TITLES,
    LAYER_ORDER,
    LAYER_TITLES,
    TIMING_LABELS,
    TIMING_LAYERS,
    UI_VERSION,
)

#: 文档路径（``docs/UI.md``）。
DOC_PATH = Path("docs/UI.md")

#: 文档里必须出现的命令行片段（可复制粘贴）。
REQUIRED_COMMANDS = (
    "liveblocks ui view --record",
    "liveblocks ui replay --record",
    "liveblocks ui report --dir",
)

#: 文档里必须出现的概念词（缺了说明某段被删）。
REQUIRED_TERMS = (
    "RunHeader",
    "run_id",
    "backend",
    "image_ref",
    "timings_ms",
    "state_delta",
    "--step",
    "LB-U0xx",
    "只读",
    "不是 mock-up",
)

#: 文档里提到的相对路径（``fixtures/…`` / ``docs/…`` / ``scripts/…``）必须真实存在。
_PATH_PATTERN = re.compile(r"`((?:fixtures|docs|scripts|src|tests)/[^`\s]*)`")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[3] if len(here.parents) > 3 else here.parent


def ui_document_gaps(doc_path: str | Path | None = None) -> tuple[str, ...]:
    """核对 ``docs/UI.md``，返回缺口列表（空元组 = 文档与实现一致）。"""
    path = Path(doc_path) if doc_path is not None else _repo_root() / DOC_PATH
    if not path.is_file():
        return (f"缺少文档 {path}",)
    text = path.read_text(encoding="utf-8")
    gaps: list[str] = []

    for layer in LAYER_ORDER:
        row = f"| `{layer}` | {LAYER_TITLES[layer]} |"
        if row not in text:
            gaps.append(f"文档缺少面板行：{row}")
    for code in ISSUE_CODES:
        row = f"| `{code}` | {ISSUE_TITLES[code]} |"
        if row not in text:
            gaps.append(f"文档缺少问题码行：{row}")
    for key in TIMING_LAYERS:
        row = f"| `{key}` | {TIMING_LABELS[key]} |"
        if row not in text:
            gaps.append(f"文档缺少分层耗时行：{row}")
    for command in REQUIRED_COMMANDS:
        if command not in text:
            gaps.append(f"文档缺少命令：{command}")
    for term in REQUIRED_TERMS:
        if term not in text:
            gaps.append(f"文档缺少概念词：{term}")
    if f"ui_version" not in text and UI_VERSION not in text:
        gaps.append(f"文档没有写 ui_version {UI_VERSION}")
    for relative in sorted(set(_PATH_PATTERN.findall(text))):
        if any(char in relative for char in "*<>"):
            continue
        if not (_repo_root() / relative).exists():
            gaps.append(f"文档提到的路径不存在：{relative}")
    return tuple(gaps)


__all__ = ["DOC_PATH", "REQUIRED_COMMANDS", "REQUIRED_TERMS", "ui_document_gaps"]
