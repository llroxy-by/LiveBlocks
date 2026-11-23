"""S4 ``Ast``（`docs/PROTOCOL.md` §5）的数据模型、``span`` / ``origin`` 约定与最小校验。

§5 只给了一个示例与两句约定，缺的东西在本模块按**加法式**（§10 规则 1）补齐，取舍写在
``docs/GRAMMAR.md`` §4 与 ``docs/DECISIONS.md`` D-016：

- ``span`` 是**闭区间**：``slot_from`` / ``slot_to`` 都指向真实槽位；单行节点写
  ``{"row": r, "slot_from": a, "slot_to": b}``（照 §5 示例），跨行节点（``Program``）改写
  ``{"row_from": r0, "row_to": r1, "slot_from": a, "slot_to": b}``；
- ``origin`` 每个节点都有：单卡节点照 §5 用 ``{"candidate_id", "card_face"}``，跨卡节点
  （``Program``）用 ``{"candidate_ids": [...], "card_faces": [...]}``；
- ``Ast`` 多一个 ``frame_id``（加法式），把语法树指回它来自哪一帧，供 LB-008 / LB-009 使用。

``validate_ast`` 与 S2 / S3 的校验同一口径：只核对 §5（+ 本实现的加法式字段）写过的东西，
不做 JSON Schema 级别的完备校验；``node`` 是开放枚举，未知节点名只要求 ``span`` + ``origin``
（§5 原文「``node`` 取值集合是开放枚举」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from .grammar import GRAMMAR_VERSION, NODE_COMMAND, NODE_NUMBER, NODE_PROGRAM
from ..vision.protocol import SCHEMA_VERSION, VERSION_PATTERN, validate_diagnostics


@dataclass(frozen=True)
class Span:
    """行 + 槽位区间（§5），闭区间。``row_to`` 为 ``None`` 表示单行节点。"""

    row_from: int
    slot_from: int
    slot_to: int
    row_to: int | None = None

    @classmethod
    def single(cls, row: int, slot_from: int, slot_to: int) -> "Span":
        return cls(row_from=int(row), slot_from=int(slot_from), slot_to=int(slot_to))

    @classmethod
    def across(cls, row_from: int, slot_from: int, row_to: int, slot_to: int) -> "Span":
        return cls(
            row_from=int(row_from),
            slot_from=int(slot_from),
            slot_to=int(slot_to),
            row_to=int(row_to),
        )

    @property
    def is_single_row(self) -> bool:
        return self.row_to is None or self.row_to == self.row_from

    def to_dict(self) -> dict[str, Any]:
        if self.is_single_row:
            return {
                "row": int(self.row_from),
                "slot_from": int(self.slot_from),
                "slot_to": int(self.slot_to),
            }
        return {
            "row_from": int(self.row_from),
            "row_to": int(self.row_to),
            "slot_from": int(self.slot_from),
            "slot_to": int(self.slot_to),
        }

    def describe(self) -> str:
        """``0:2`` / ``0..1:2`` 这种简短形式，用于诊断消息与报告。"""
        if self.is_single_row:
            return f"{self.row_from}:{self.slot_from}-{self.slot_to}"
        return f"{self.row_from}..{self.row_to}:{self.slot_from}-{self.slot_to}"


@dataclass(frozen=True)
class Origin:
    """来源卡片（§5）：单卡节点用 ``candidate_id`` / ``card_face``，跨卡节点用两个数组。"""

    entries: tuple[tuple[str, str], ...] = ()
    as_list: bool = False

    @classmethod
    def single(cls, candidate_id: str, card_face: str) -> "Origin":
        return cls(entries=((str(candidate_id), str(card_face)),), as_list=False)

    @classmethod
    def many(cls, entries: "tuple[tuple[str, str], ...] | list[tuple[str, str]]") -> "Origin":
        return cls(entries=tuple((str(a), str(b)) for a, b in entries), as_list=True)

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(candidate_id for candidate_id, _face in self.entries)

    @property
    def card_faces(self) -> tuple[str, ...]:
        return tuple(face for _candidate_id, face in self.entries)

    def to_dict(self) -> dict[str, Any]:
        if not self.as_list and len(self.entries) == 1:
            candidate_id, card_face = self.entries[0]
            return {"candidate_id": candidate_id, "card_face": card_face}
        return {
            "candidate_ids": [candidate_id for candidate_id, _face in self.entries],
            "card_faces": [face for _candidate_id, face in self.entries],
        }


@dataclass(frozen=True)
class Number:
    """§5 ``Number``：一个字面数字卡的值。"""

    span: Span
    origin: Origin
    value: int

    node = NODE_NUMBER

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": NODE_NUMBER,
            "span": self.span.to_dict(),
            "value": int(self.value),
            "origin": self.origin.to_dict(),
        }


@dataclass(frozen=True)
class Command:
    """§5 ``Command``：一张命令卡 + 紧跟其后的字面参数卡。"""

    span: Span
    origin: Origin
    name: str
    args: tuple[Number, ...] = ()

    node = NODE_COMMAND

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": NODE_COMMAND,
            "span": self.span.to_dict(),
            "name": self.name,
            "args": [argument.to_dict() for argument in self.args],
            "origin": self.origin.to_dict(),
        }


@dataclass(frozen=True)
class Program:
    """§5 ``Program``：按 ``row_index`` 顺序排列的语句序列，是 ``Ast`` 的根。"""

    span: Span
    origin: Origin
    body: tuple[Command, ...] = ()

    node = NODE_PROGRAM

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": NODE_PROGRAM,
            "span": self.span.to_dict(),
            "body": [statement.to_dict() for statement in self.body],
            "origin": self.origin.to_dict(),
        }

    @property
    def commands(self) -> tuple[Command, ...]:
        return self.body


@dataclass(frozen=True)
class Ast:
    """§5 ``Ast``：语法树 + 诊断 + 版本三元组。"""

    root: Program
    frame_id: str
    diagnostics: tuple[dict[str, Any], ...] = ()
    grammar_version: str = GRAMMAR_VERSION
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": "Ast",
            "frame_id": self.frame_id,
            "grammar_version": self.grammar_version,
            "root": self.root.to_dict(),
            "diagnostics": [dict(entry) for entry in self.diagnostics],
        }

    def codes(self) -> tuple[str, ...]:
        return tuple(str(entry.get("code", "")) for entry in self.diagnostics)

    def diagnostics_with(self, code: str) -> tuple[dict[str, Any], ...]:
        return tuple(entry for entry in self.diagnostics if entry.get("code") == code)

    @property
    def statements(self) -> tuple[Command, ...]:
        return self.root.body

    def nodes(self) -> Iterator[dict[str, Any]]:
        """遍历**序列化后**的全部节点（含根），供测试与自检核对 span / origin。"""
        yield from _iter_documents(self.root.to_dict())


def _iter_documents(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("body", []):  # Program
        yield from _iter_documents(child)
    for child in node.get("args", []):  # Command
        yield from _iter_documents(child)


def node_count(ast: Ast) -> int:
    return sum(1 for _ in ast.nodes())


def _check_span(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: span 必须是对象")
    has_row = "row" in value
    has_range = "row_from" in value or "row_to" in value
    if has_row and has_range:
        raise ValueError(f"{where}: span 不能同时给 row 与 row_from/row_to")
    if has_row:
        keys = ("row", "slot_from", "slot_to")
    elif has_range:
        keys = ("row_from", "row_to", "slot_from", "slot_to")
    else:
        raise ValueError(f"{where}: span 必须给出 row 或 row_from/row_to")
    for key in keys:
        number = value.get(key)
        if not isinstance(number, int) or isinstance(number, bool):
            raise ValueError(f"{where}: span.{key} 必须是整数，实际 {number!r}")
        if number < 0:
            raise ValueError(f"{where}: span.{key} 不能为负，实际 {number!r}")
    if value["slot_from"] > value["slot_to"]:
        raise ValueError(f"{where}: span.slot_from 不能大于 slot_to")
    if has_range and value["row_from"] > value["row_to"]:
        raise ValueError(f"{where}: span.row_from 不能大于 row_to")


def _check_origin(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: origin 必须是对象")
    if "candidate_ids" in value:
        ids = value["candidate_ids"]
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise ValueError(f"{where}: origin.candidate_ids 必须是字符串数组")
        faces = value.get("card_faces")
        if faces is not None:
            if not isinstance(faces, list) or not all(isinstance(item, str) for item in faces):
                raise ValueError(f"{where}: origin.card_faces 必须是字符串数组")
            if len(faces) != len(ids):
                raise ValueError(f"{where}: origin.card_faces 与 candidate_ids 长度必须一致")
        return
    if "candidate_id" in value:
        if not isinstance(value["candidate_id"], str) or not value["candidate_id"]:
            raise ValueError(f"{where}: origin.candidate_id 必须是非空字符串")
        if not isinstance(value.get("card_face"), str):
            raise ValueError(f"{where}: origin.card_face 必须是字符串")
        return
    raise ValueError(f"{where}: origin 必须给出 candidate_id 或 candidate_ids")


def _check_node(node: Any, where: str) -> None:
    if not isinstance(node, dict):
        raise ValueError(f"{where}: 节点必须是对象")
    kind = node.get("node")
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"{where}: 节点必须有非空字符串 node 字段")
    if "span" not in node:
        raise ValueError(f"{where}: 节点 {kind} 缺少 span（§5 要求每个节点都带 span）")
    if "origin" not in node:
        raise ValueError(f"{where}: 节点 {kind} 缺少 origin（§5 要求每个节点都带 origin）")
    _check_span(node["span"], where)
    _check_origin(node["origin"], where)

    if kind == NODE_COMMAND:
        if not isinstance(node.get("name"), str) or not node.get("name"):
            raise ValueError(f"{where}: Command.name 必须是非空字符串")
        args = node.get("args")
        if not isinstance(args, list):
            raise ValueError(f"{where}: Command.args 必须是数组")
        for position, argument in enumerate(args):
            _check_node(argument, f"{where}.args[{position}]")
        return
    if kind == NODE_NUMBER:
        value = node.get("value")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{where}: Number.value 必须是整数，实际 {value!r}")
        return
    if kind == NODE_PROGRAM:
        body = node.get("body")
        if not isinstance(body, list):
            raise ValueError(f"{where}: Program.body 必须是数组")
        for position, statement in enumerate(body):
            _check_node(statement, f"{where}.body[{position}]")
        return
    # node 是开放枚举（§5）：未知节点名只要求 span + origin，上面已经查过。


def validate_ast(document: dict[str, Any]) -> None:
    """校验 ``Ast``（§5 + 本实现的加法式 ``frame_id`` / 跨行 span / 跨卡 origin）。"""
    if not isinstance(document, dict):
        raise ValueError("Ast 必须是对象")
    if document.get("kind") != "Ast":
        raise ValueError(f"kind 必须是 'Ast'，实际 {document.get('kind')!r}")
    version = document.get("schema_version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"schema_version 必须是 'major.minor[.patch]' 字符串，实际 {version!r}")
    grammar_version = document.get("grammar_version")
    if not isinstance(grammar_version, str) or not VERSION_PATTERN.fullmatch(grammar_version):
        raise ValueError(f"grammar_version 必须是 'major.minor[.patch]' 字符串，实际 {grammar_version!r}")
    if not isinstance(document.get("frame_id"), str):
        raise ValueError("frame_id 必须是字符串（本实现的加法式字段）")
    if "root" not in document:
        raise ValueError("Ast 缺少字段 'root'")
    root = document["root"]
    if not isinstance(root, dict) or root.get("node") != NODE_PROGRAM:
        raise ValueError("Ast.root 必须是 node = 'Program' 的对象")
    _check_node(root, "root")
    validate_diagnostics(document.get("diagnostics", []))
    for position, entry in enumerate(document.get("diagnostics", [])):
        where = f"diagnostics[{position}]"
        if "span" in entry:
            _check_span(entry["span"], where)
        if "origin" in entry:
            _check_origin(entry["origin"], where)


__all__ = [
    "Ast",
    "Command",
    "Number",
    "Origin",
    "Program",
    "Span",
    "node_count",
    "validate_ast",
]
