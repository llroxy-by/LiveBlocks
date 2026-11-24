"""``docs/CARD-SPEC.md`` 与实现的**双向核对**。

文档核对要能抓住三种「假通过」：

1. **文档写了、实现没做**：规范里的数字与代码里的常量/数据对不上 → :func:`number_gaps`；
2. **实现有码、文档没写**：``LB-S0xx`` 码表、章节标题缺了 → :func:`code_gaps` / :func:`section_gaps`；
3. **数字来路不明**：表格里冒出一行「实现里没有的数字」，或正文里出现一个追不到任何出处的
   「数字 + 单位」 → :func:`orphan_gaps` / :func:`prose_gaps`。

另外核对「凡无实测依据的项必须显式标「待实测」」：排版参数与实体项在表格里必须带
「待实测」，:func:`number_gaps` 会逐项查。
"""

from __future__ import annotations

import re
import shlex

from .codes import document_gaps as code_gaps
from .spec import CardSheetSpec, all_numbers, generation_numbers, spec_numbers, unmeasured_items

#: 文档必须有的章节标题（``docs/CARD-SPEC.md`` 的骨架）。
REQUIRED_SECTIONS: tuple[str, ...] = (
    "## 0. 范围与边界",
    "## 1. 一条命令",
    "## 2. 卡片尺寸与标记尺寸（数字溯源）",
    "## 3. 静区规则",
    "## 4. 基材（待实测）",
    "## 5. 打印工艺（待实测）",
    "## 6. 批次编号",
    "## 7. 质检项（QC）",
    "## 8. 标记页排版（A4）",
    "## 9. 手绘降级版",
    "## 10. 诊断码（`LB-S0xx`）",
    "## 11. 待人工验证",
    "## 12. 已知限制",
    "## 13. 协议缺口（本单只报告、不改 `docs/PROTOCOL.md`）",
)

#: 数字溯源表所在的章节（起点标题 → 下一个 ``## `` 为止）。
TRACE_SECTION = "## 2. 卡片尺寸与标记尺寸（数字溯源）"

#: 会参与「正文数字必须有出处」核对的数量单位。
_UNITS = ("mm", "px/mm", "模块", "张", "页")
_NUMBER_WITH_UNIT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(mm|px/mm|模块|张|页)(?![\d])")
#: 代码块里「以 ``liveblocks`` 开头的一行」就是一条要核对的命令。
_COMMAND = re.compile(r"^\s*liveblocks\s+(.+)$")


def default_spec() -> CardSheetSpec:
    return CardSheetSpec.build()


def _all_numbers():
    spec = default_spec()
    return all_numbers(spec)


def number_gaps(markdown: str) -> list[str]:
    """每一项可溯源数字 / 排版参数 / 待实测项都必须按「数字 + 单位」出现在文档里。

    - 可溯源项：同一行必须同时出现「标签」与「数值 + 单位」；
    - 排版参数与待实测项：同一行必须出现「标签」与「待实测」（无实测依据必须显式标注）。
    """
    gaps: list[str] = []
    lines = markdown.splitlines()
    for entry in _all_numbers():
        matching = [line for line in lines if entry.label in line and line.lstrip().startswith("|")]
        if not matching:
            gaps.append(f"「{entry.label}」没有出现在文档的数字表里")
            continue
        if entry.measured:
            if not any(entry.value_text in line for line in matching):
                gaps.append(
                    f"「{entry.label}」的数值「{entry.value_text}」没有出现在文档里（实现与此不一致？）"
                )
            continue
        if not any("待实测" in line for line in matching):
            gaps.append(f"「{entry.label}」没有实测依据，但文档里没有标「待实测」")
    return gaps


def section_gaps(markdown: str) -> list[str]:
    return [f"缺少章节「{heading}」" for heading in REQUIRED_SECTIONS if heading not in markdown]


def _trace_section(markdown: str) -> str:
    start = markdown.find(TRACE_SECTION)
    if start < 0:
        return ""
    rest = markdown[start + len(TRACE_SECTION) :]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def orphan_gaps(markdown: str) -> list[str]:
    """数字溯源表里不许有「实现里没有的行」（防止文档自己发明数字）。"""
    section = _trace_section(markdown)
    if not section:
        return [f"没有找到「{TRACE_SECTION}」章节"]
    known = {entry.label for entry in _all_numbers()}
    gaps: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or set(stripped) <= set("|-: "):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells:
            continue
        label = cells[0].strip("` ")
        if not label or label in ("项", "标签"):
            continue
        if label not in known:
            gaps.append(f"数字表里的「{label}」在实现里查不到（没有出处就是编的）")
    return gaps


def prose_gaps(markdown: str) -> list[str]:
    """正文里出现的「数字 + 单位」必须能在数字表的数值里找到（防手写新数字）。"""
    allowed_digits: set[str] = set()
    for entry in _all_numbers():
        for token in re.findall(r"\d+(?:\.\d+)?", entry.value_text):
            allowed_digits.add(token)
    gaps: list[str] = []
    seen: set[str] = set()
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):  # 表格行由 number_gaps / orphan_gaps 负责
            continue
        for match in _NUMBER_WITH_UNIT.finditer(line):
            token = match.group(1)
            if token in allowed_digits:
                continue
            text = match.group(0)
            if text in seen:
                continue
            seen.add(text)
            gaps.append(f"正文里的「{text}」在数字表里没有出处（要么补一行溯源，要么改写）")
    return gaps


def fenced_blocks(markdown: str) -> list[str]:
    """取出文档里的代码块内容（```/~~~ 围栏），返回每个块的正文。"""
    blocks: list[str] = []
    current: list[str] | None = None
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def command_gaps(markdown: str) -> list[str]:
    """**代码块里**的 ``liveblocks …`` 命令必须真能被命令行解析器接受。

    只查代码块（可复制的命令）；正文里 ``liveblocks recognize detect`` 这种「提一句命令名」的
    写法不算完整命令，不参与解析核对。
    """
    from .. import cli

    parser = cli.build_parser()
    gaps: list[str] = []
    commands: set[str] = set()
    for block in fenced_blocks(markdown):
        for line in block.splitlines():
            match = _COMMAND.match(line)
            if not match:
                continue
            text = match.group(1)
            if "#" in text:  # 行尾的注释不算命令参数
                text = text.split("#")[0]
            commands.add(text.strip())
    for command in sorted(commands):
        try:
            parser.parse_args(shlex.split(command))
        except SystemExit:
            gaps.append(f"文档里的命令 `liveblocks {command}` 解析失败")
        except Exception as error:  # pragma: no cover - 防御
            gaps.append(f"文档里的命令 `liveblocks {command}` 出错：{error}")
    return gaps


def card_spec_document_gaps(markdown: str) -> list[str]:
    """全部缺口的并集（空 = 文档与实现一致）。"""
    return (
        code_gaps(markdown)
        + section_gaps(markdown)
        + number_gaps(markdown)
        + orphan_gaps(markdown)
        + prose_gaps(markdown)
        + command_gaps(markdown)
    )


__all__ = [
    "REQUIRED_SECTIONS",
    "TRACE_SECTION",
    "card_spec_document_gaps",
    "code_gaps",
    "command_gaps",
    "default_spec",
    "generation_numbers",
    "number_gaps",
    "orphan_gaps",
    "prose_gaps",
    "section_gaps",
    "spec_numbers",
    "unmeasured_items",
]
