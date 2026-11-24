"""``docs/CAPTURE.md`` 与实现的核对（文档缺口）。

与其他层的 ``*_document_gaps`` 同一思路：**入库文档必须能被机器核对**——路由、命令、错误码、
``kind``、落盘文件名、以及文档里提到的路径是否真实存在。段落被删/被改写时这里会报缺口，
``tests/test_serve_cam_docs.py`` 还会做反向验证（先删一行再断言必须报缺口），防止核对函数
本身变成永真式。

唯一的例外是 Issue #26 的验收④（手机上选后置摄像头对着实体卡验证）：它**只能人验**，
文档里必须留着「待人工验证」与人工步骤，测试会断言这句话没被悄悄改成「已通过」。
"""

from __future__ import annotations

import re
from pathlib import Path

from .protocol import (
    CAPTURE_FILES,
    CAPTURE_VERSION,
    ERROR_CODES,
    KIND_CAPTURE,
    KIND_CAPTURE_INDEX,
    KIND_CAPTURE_LIST,
    KIND_CAPTURE_RESULT,
    KIND_CAPTURE_SCENE,
    KIND_ERROR,
    KIND_FRAME_RECOGNITION,
    KIND_HEALTH,
    KIND_INFO,
    MEDIA_GRAY,
    MEDIA_JSON,
    MEDIA_PNG,
    OUTCOMES,
    REFUSED_DIRNAME,
    ROUTE_CAPTURE,
    ROUTE_CAPTURES,
    ROUTE_FRAME,
    ROUTE_HEALTH,
    ROUTE_INDEX,
    ROUTE_INFO,
    SERVICE_NAME,
)

#: 文档路径。
DOC_PATH = Path("docs/CAPTURE.md")

#: 文档里必须出现的命令行片段（可复制粘贴）。
REQUIRED_COMMANDS = (
    "liveblocks serve-cam serve",
    "liveblocks serve-cam page",
    "liveblocks recognize report --dir",
    "pytest tests/test_serve_cam.py",
)

#: 文档里必须出现的 HTTP 路由。
REQUIRED_ROUTES = (
    ROUTE_INDEX,
    ROUTE_HEALTH,
    ROUTE_INFO,
    ROUTE_FRAME,
    ROUTE_CAPTURE,
    ROUTE_CAPTURES,
)

#: 文档里必须出现的 ``kind``。
REQUIRED_KINDS = (
    KIND_HEALTH,
    KIND_INFO,
    KIND_FRAME_RECOGNITION,
    KIND_CAPTURE,
    KIND_CAPTURE_RESULT,
    KIND_CAPTURE_LIST,
    KIND_CAPTURE_SCENE,
    KIND_CAPTURE_INDEX,
    KIND_ERROR,
)

#: 文档里必须出现的媒体类型与落盘文件。
REQUIRED_MEDIA = (MEDIA_PNG, MEDIA_GRAY, MEDIA_JSON)
REQUIRED_FILES = CAPTURE_FILES

#: 文档里必须出现的概念词（缺了说明某段被删）。
REQUIRED_TERMS = (
    SERVICE_NAME,
    f"`capture_version` {CAPTURE_VERSION}",
    "getUserMedia",
    "CardCandidateList",
    "fixtures/live/<时间戳>/",
    "refused",
    "待人工验证",
    "零新增依赖",
    "不做公网鉴权",
)

#: 文档里提到的相对路径（``fixtures/…`` / ``docs/…`` / ``src/…`` / ``tests/…``）必须真实存在。
_PATH_PATTERN = re.compile(r"`((?:fixtures|docs|scripts|src|tests)/[^`\s]*)`")

#: 文档里可以为「将来才会有」的路径白名单（采集目录是人采的，入库时不存在）。
PATH_ALLOWLIST = ("fixtures/live", f"fixtures/live/{REFUSED_DIRNAME}")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[3] if len(here.parents) > 3 else here.parent


def capture_document_gaps(doc_path: str | Path | None = None) -> tuple[str, ...]:
    """核对 ``docs/CAPTURE.md``，返回缺口列表（空元组 = 文档与实现一致）。"""
    path = Path(doc_path) if doc_path is not None else _repo_root() / DOC_PATH
    if not path.is_file():
        return (f"缺少文档 {path}",)
    text = path.read_text(encoding="utf-8")
    gaps: list[str] = []

    for route in REQUIRED_ROUTES:
        if route not in text:
            gaps.append(f"文档缺少路由：{route}")
    for kind in REQUIRED_KINDS:
        if f"`{kind}`" not in text:
            gaps.append(f"文档缺少 kind：{kind}")
    for media in REQUIRED_MEDIA:
        if media not in text:
            gaps.append(f"文档缺少请求体媒体类型：{media}")
    for file_name in REQUIRED_FILES:
        if file_name not in text:
            gaps.append(f"文档缺少落盘文件名：{file_name}")
    for outcome in OUTCOMES:
        if outcome not in text:
            gaps.append(f"文档缺少采集结论：{outcome}")
    for code, title in ERROR_CODES.items():
        row = f"| `{code}` | {title} |"
        if row not in text:
            gaps.append(f"文档缺少错误码行：{row}")
    for command in REQUIRED_COMMANDS:
        if command not in text:
            gaps.append(f"文档缺少命令：{command}")
    for term in REQUIRED_TERMS:
        if term not in text:
            gaps.append(f"文档缺少概念词：{term}")

    root = _repo_root()
    for match in sorted(set(_PATH_PATTERN.findall(text))):
        cleaned = match.rstrip(".,;:)/")
        if cleaned in PATH_ALLOWLIST:
            continue
        if "<" in cleaned or "*" in cleaned:
            continue
        if not (root / cleaned).exists():
            gaps.append(f"文档提到的路径不存在：{cleaned}")
    return tuple(gaps)


__all__ = [
    "DOC_PATH",
    "PATH_ALLOWLIST",
    "REQUIRED_COMMANDS",
    "REQUIRED_FILES",
    "REQUIRED_KINDS",
    "REQUIRED_MEDIA",
    "REQUIRED_ROUTES",
    "REQUIRED_TERMS",
    "capture_document_gaps",
]
