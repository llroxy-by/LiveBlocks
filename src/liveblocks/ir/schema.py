"""§6 ``Ir`` 的 JSON Schema 与**零依赖**的最小 schema 校验器。

协议 §6 只说「IR 通过 schema 校验」，没有给 schema 文件；D-015 / D-016 都写着「完整的 schema 工具
留给 LB-006 / LB-009」。本模块把它补上：

1. schema 是**入库的数据文件** ``src/liveblocks/data/ir_schema.json``（JSON Schema draft 2020-12），
   随 wheel 一起安装（``pyproject.toml`` 的 ``liveblocks.data`` 已包含 ``*.json``）；
2. 校验器只用标准库，支持本 schema 用到的那一小撮关键字（:data:`SUPPORTED_KEYWORDS`）。schema
   文件里一旦出现不支持的关键字，:func:`schema_unsupported_keywords` 会报出来，测试断言为空——
   这样「schema 文件被真的按它写的规则校验过」不会变成假通过；
3. 语义检查（哪个 ``op`` 收哪个参数、``step_id`` 是否密集连续、诊断码是否落在 ``LB-I0xx``）不属于
   schema 的表达能力，放在 :mod:`liveblocks.ir.protocol` 的 ``validate_ir`` 里。

装得上 ``jsonschema`` 时，``tests/test_ir_protocol.py`` 会用它当**独立实现**交叉核对同一批文档
（与 LB-003 用 OpenCV 交叉核对同一思路）；CI 只装 ``.[dev]``，那组用例自动跳过。
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from ..data import read_json
from .ops import SCHEMA_FILENAME

#: 本校验器支持的 JSON Schema 关键字。schema 文件只许用这些。
SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
    }
)

#: JSON Schema 的 ``type`` 取值 → Python 侧判定。
#:
#: ``integer`` 按 JSON Schema 的语义认「没有小数部分的数」（``1`` 与 ``1.0`` 都算整数），但排除
#: ``bool``（Python 里 ``True`` 是 ``int``）；本实现产出的 ``Ir`` 里不会有浮点，``validate_ir`` 的
#: 语义检查另外要求 ``args`` / ``source`` / ``limits`` 是真正的 ``int``。
_TYPE_CHECKS: dict[str, Any] = {
    "object": lambda value: isinstance(value, dict),
    "array": lambda value: isinstance(value, list),
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: (
        isinstance(value, int) and not isinstance(value, bool)
    )
    or (isinstance(value, float) and value.is_integer()),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "null": lambda value: value is None,
}


class SchemaError(ValueError):
    """文档不符合 schema（``ValueError`` 的子类，调用方按「输入不合法」处理）。"""


def load_schema() -> dict[str, Any]:
    """读取入库的 IR schema（每次调用都从包内数据读，测试可以放心改内存副本）。"""
    document = read_json(SCHEMA_FILENAME)
    if not isinstance(document, dict):
        raise SchemaError(f"{SCHEMA_FILENAME} 必须是 JSON 对象")
    return document


def iter_keywords(node: Any) -> Iterator[str]:
    """遍历 schema 里出现的全部关键字（用于「schema 有没有用到不支持的词」这项自检）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from iter_keywords(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_keywords(item)


def schema_unsupported_keywords(schema: dict[str, Any]) -> list[str]:
    """schema 里用到的、本校验器不支持的关键字（排序后返回；空列表 = 都在支持范围内）。

    ``properties`` / ``$defs`` 的**键**是用户自定义名字（例如 ``step``、``distance``），不是关键字，
    这里按「父关键字是 properties / $defs / enum 的键」跳过。
    """
    unsupported: set[str] = set()

    def walk(node: Any, *, names_are_data: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if not names_are_data and key not in SUPPORTED_KEYWORDS:
                    unsupported.add(str(key))
                walk(value, names_are_data=key in ("properties", "$defs"))
        elif isinstance(node, list):
            for item in node:
                walk(item, names_are_data=False)

    walk(schema, names_are_data=False)
    return sorted(unsupported)


def _resolve_ref(root: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise SchemaError(f"只支持文档内引用（#/…），实际 {ref!r}")
    node: Any = root
    for raw in ref[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise SchemaError(f"引用 {ref!r} 在 schema 里不存在")
        node = node[key]
    return node


def _check_type(value: Any, expected: Any, where: str) -> None:
    names = [expected] if isinstance(expected, str) else list(expected)
    for name in names:
        check = _TYPE_CHECKS.get(name)
        if check is None:
            raise SchemaError(f"{where}: schema 用了未知的 type {name!r}")
        if check(value):
            return
    raise SchemaError(f"{where}: 类型必须是 {' / '.join(names)}，实际 {type(value).__name__}")


def validate_document(document: Any, schema: dict[str, Any] | None = None, *, where: str = "$") -> None:
    """按 schema 校验文档；不符合时抛 :class:`SchemaError`（消息里带出错位置）。"""
    root = load_schema() if schema is None else schema
    _validate(document, root, root, where)


def _validate(value: Any, node: Any, root: dict[str, Any], where: str) -> None:
    if node is True or node == {}:
        return
    if node is False:
        raise SchemaError(f"{where}: 这里不允许出现任何值")
    if not isinstance(node, dict):
        raise SchemaError(f"{where}: schema 节点必须是对象或布尔值")

    if "$ref" in node:
        _validate(value, _resolve_ref(root, str(node["$ref"])), root, where)

    if "type" in node:
        _check_type(value, node["type"], where)
    if "const" in node and value != node["const"]:
        raise SchemaError(f"{where}: 必须是 {node['const']!r}，实际 {value!r}")
    if "enum" in node and value not in node["enum"]:
        raise SchemaError(f"{where}: 必须是 {node['enum']} 之一，实际 {value!r}")

    if isinstance(value, str):
        if "minLength" in node and len(value) < int(node["minLength"]):
            raise SchemaError(f"{where}: 字符串长度必须 ≥ {node['minLength']}，实际 {len(value)}")
        if "maxLength" in node and len(value) > int(node["maxLength"]):
            raise SchemaError(f"{where}: 字符串长度必须 ≤ {node['maxLength']}，实际 {len(value)}")
        if "pattern" in node and re.search(str(node["pattern"]), value) is None:
            raise SchemaError(f"{where}: 字符串 {value!r} 不匹配 {node['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in node and value < node["minimum"]:
            raise SchemaError(f"{where}: 必须 ≥ {node['minimum']}，实际 {value!r}")
        if "maximum" in node and value > node["maximum"]:
            raise SchemaError(f"{where}: 必须 ≤ {node['maximum']}，实际 {value!r}")

    if isinstance(value, list):
        if "minItems" in node and len(value) < int(node["minItems"]):
            raise SchemaError(f"{where}: 数组长度必须 ≥ {node['minItems']}，实际 {len(value)}")
        if "maxItems" in node and len(value) > int(node["maxItems"]):
            raise SchemaError(f"{where}: 数组长度必须 ≤ {node['maxItems']}，实际 {len(value)}")
        if "items" in node:
            for position, item in enumerate(value):
                _validate(item, node["items"], root, f"{where}[{position}]")

    if isinstance(value, dict):
        for key in node.get("required", []):
            if key not in value:
                raise SchemaError(f"{where}: 缺少必需字段 {key!r}")
        properties = node.get("properties", {})
        extra = node.get("additionalProperties", True)
        for key, item in value.items():
            child = f"{where}.{key}"
            if key in properties:
                _validate(item, properties[key], root, child)
            elif extra is False:
                raise SchemaError(f"{child}: 不允许出现这个字段")
            elif isinstance(extra, dict):
                _validate(item, extra, root, child)


__all__ = [
    "SUPPORTED_KEYWORDS",
    "SchemaError",
    "iter_keywords",
    "load_schema",
    "schema_unsupported_keywords",
    "validate_document",
]
