"""``docs/E2E.md`` 与实现的核对（文档缺口）。

与其他层的 ``*_document_gaps`` 同一思路：**入库文档必须能被机器核对**——六阶段表、
``LB-E0xx`` 码表、``RunHeader`` 必填字段、两个固定时间上界、可复制粘贴的命令，以及文档里
提到的路径是否真实存在。段落被删/被改写时这里会报缺口，``tests/test_e2e_docs.py``
还会做反向验证（先删一行再断言必须报缺口），防止核对函数本身变成永真式。
"""

from __future__ import annotations

import re
from pathlib import Path

from .protocol import (
    CODE_TITLES,
    E2E_CODES,
    E2E_VERSION,
    HEADER_REQUIRED_FIELDS,
    RECORD_BOUND_MS,
    STAGES,
    STAGE_LABELS,
    TIME_BOUND_MS,
)

#: 文档路径（``docs/E2E.md``）。
DOC_PATH = Path("docs/E2E.md")

#: 文档里必须出现的命令行片段（可复制粘贴）。
REQUIRED_COMMANDS = (
    "liveblocks e2e run --scene",
    "liveblocks e2e regression --dir",
    "--update-baseline",
    "--corrupt layout",
    "liveblocks ui view --record",
)

#: 文档里必须出现的概念词（缺了说明某段被删）。
REQUIRED_TERMS = (
    "RunHeader",
    "JSONL",
    "canonical JSON",
    "LB-E0xx",
    "最早失败层",
    "失败摘要",
    "不是性能结论",
    "不接真实摄像头",
    "scripts/build_run_records.py",
    "--no-self-check",
    "fixtures/e2e/baseline.json",
)

#: 文档里提到的相对路径（``fixtures/…`` / ``docs/…`` / ``scripts/…``）必须真实存在。
_PATH_PATTERN = re.compile(r"`((?:fixtures|docs|scripts|src|tests)/[^`\s]*)`")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[3] if len(here.parents) > 3 else here.parent


def e2e_document_gaps(doc_path: str | Path | None = None) -> tuple[str, ...]:
    """核对 ``docs/E2E.md``，返回缺口列表（空元组 = 文档与实现一致）。"""
    path = Path(doc_path) if doc_path is not None else _repo_root() / DOC_PATH
    if not path.is_file():
        return (f"缺少文档 {path}",)
    text = path.read_text(encoding="utf-8")
    gaps: list[str] = []

    for stage in STAGES:
        row = f"| `{stage}` | {STAGE_LABELS[stage]} |"
        if row not in text:
            gaps.append(f"文档缺少阶段行：{row}")
    for code in E2E_CODES:
        row = f"| `{code}` | {CODE_TITLES[code]} |"
        if row not in text:
            gaps.append(f"文档缺少问题码行：{row}")
    for field in HEADER_REQUIRED_FIELDS:
        if f"`{field}`" not in text:
            gaps.append(f"文档缺少 RunHeader 必填字段：{field}")
    for value in (f"{TIME_BOUND_MS:g}", f"{RECORD_BOUND_MS:g}"):
        if value not in text:
            gaps.append(f"文档没有写固定时间上界 {value} ms")
    for command in REQUIRED_COMMANDS:
        if command not in text:
            gaps.append(f"文档缺少命令：{command}")
    for term in REQUIRED_TERMS:
        if term not in text:
            gaps.append(f"文档缺少概念词：{term}")
    if "e2e_version" not in text and E2E_VERSION not in text:
        gaps.append(f"文档没有写 e2e_version {E2E_VERSION}")
    if "草案" not in text:
        gaps.append("文档没有标注「草案」状态")
    for relative in sorted(set(_PATH_PATTERN.findall(text))):
        if any(char in relative for char in "*<>"):
            continue
        if not (_repo_root() / relative).exists():
            gaps.append(f"文档提到的路径不存在：{relative}")
    return tuple(gaps)


__all__ = ["DOC_PATH", "REQUIRED_COMMANDS", "REQUIRED_TERMS", "e2e_document_gaps"]
