"""§8 运行记录（JSONL）的**写**与容错读，以及跨运行可比的层指纹。

与 LB-008 的 ``liveblocks.ui.record``（只读、容错渲染）互补：

- 这里负责**生产**：把一次全链路的层文档按 §8 顺序写成 JSONL（首行 ``RunHeader``，
  随后 ``MarkerCandidateList`` / ``CardCandidateList`` / ``Layout`` / ``Ast`` / ``Ir`` /
  ``ExecEvent``* / ``ExecResult``），一行一个对象、UTF-8、末尾一个换行；
- 读回来时一行坏掉只报 ``LB-E008``，其余行照常返回（便于回归自检自己的产物）；
- **层指纹** :func:`fingerprint` 用「canonical JSON（键排序）+ 换行 + sha256」，
  因此同一层文档两次运行得到同一个 64 位十六进制串，与机器、哈希随机序、键的插入顺序无关。
  黄金基准（``fixtures/e2e/baseline.json``）比对的就是它；``RunHeader.timings_ms``
  是记录时实测的墙钟耗时，**不进指纹**（不同机器必然不同）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .protocol import CODE_BAD_LINE, E2eIssue, issue


def canonical_json(document: Any) -> str:
    """键排序、无多余空白的 JSON（指纹与逐字节比对的唯一口径）。"""
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def jsonl_line(document: dict[str, Any]) -> str:
    """写盘用的单行 JSON：保留字段插入顺序（读起来跟产出顺序一致），不排序键。"""
    return json.dumps(document, ensure_ascii=False)


def record_text(documents: list[dict[str, Any]]) -> str:
    """把一串记录文档拼成 JSONL 文本（每行一个，末尾一个换行）。"""
    return "".join(jsonl_line(document) + "\n" for document in documents)


def write_record(path: str | Path, documents: list[dict[str, Any]]) -> Path:
    """把记录写到 ``path``（父目录自动创建），返回绝对化后的路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(record_text(documents), encoding="utf-8")
    return target


def read_documents(path: str | Path) -> tuple[list[dict[str, Any]], list[E2eIssue]]:
    """读一份 JSONL 记录：坏行报 ``LB-E008`` 并跳过，返回 ``(文档列表, 问题列表)``。"""
    source = Path(path)
    documents: list[dict[str, Any]] = []
    issues: list[E2eIssue] = []
    for number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as error:
            issues.append(
                issue(
                    CODE_BAD_LINE,
                    f"第 {number} 行不是合法 JSON：{error}",
                    line=number,
                )
            )
            continue
        if not isinstance(document, dict):
            issues.append(
                issue(
                    CODE_BAD_LINE,
                    f"第 {number} 行不是 JSON 对象（{type(document).__name__}）",
                    line=number,
                )
            )
            continue
        documents.append(document)
    return documents, issues


def sha256_bytes(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def fingerprint(documents: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    """一组层文档的指纹：``sha256(canonical_json(doc) + "\\n" ...)``（空集也有确定值）。"""
    return sha256_text("".join(canonical_json(document) + "\n" for document in documents))


def short_hash(value: str, *, length: int = 12) -> str:
    """失败摘要里显示的短指纹。"""
    return value[:length] if len(value) > length else value


__all__ = [
    "canonical_json",
    "jsonl_line",
    "record_text",
    "write_record",
    "read_documents",
    "sha256_bytes",
    "sha256_text",
    "fingerprint",
    "short_hash",
]
