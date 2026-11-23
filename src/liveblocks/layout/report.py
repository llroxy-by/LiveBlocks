"""排列层自检报告：把 ``fixtures/layouts/`` 里的候选夹具全部排一遍，输出可读摘要与验收对照。

与 LB-003 的 ``recognize report`` 同一思路：**证据来自真跑一遍**，而不是「应该能过」。
报告里每一行都能追到候选 id（行 → 槽位 → ``candidate_id``），诊断按 §9 的形状列出。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .arrange import LayoutOptions, candidates_from_document, layout_from_document
from .protocol import DIAG_DUPLICATE_REMOVED, DIAG_SPACING_AMBIGUOUS, Layout, validate_layout

#: 夹具文件名里出现这些片段就认为它覆盖对应验收点（约定见 ``fixtures/README.md`` §4）。
TWO_ROW_TAGS = ("multi-row", "two-row")
SPACING_TAGS = ("spacing",)
DUPLICATE_TAGS = ("duplicate",)


@dataclass(frozen=True)
class FixtureLayout:
    """一个候选夹具的排列结果 + 校验结论。"""

    name: str
    path: Path
    input_ids: tuple[str, ...]
    layout: Layout
    validation_errors: tuple[str, ...] = ()

    @property
    def codes(self) -> tuple[str, ...]:
        return self.layout.codes()

    @property
    def placed_ids(self) -> tuple[str, ...]:
        return self.layout.candidate_order

    @property
    def flagged_ids(self) -> tuple[str, ...]:
        """被诊断点名过的候选 id（去重后按出现顺序）。"""
        seen: list[str] = []
        for entry in self.layout.diagnostics:
            for candidate_id in entry.get("candidate_ids", []):
                if candidate_id not in seen:
                    seen.append(candidate_id)
        return tuple(seen)

    @property
    def untraced_ids(self) -> tuple[str, ...]:
        """输入里有、但既没进槽位也没被诊断点名 —— 「排序结论可追溯」的漏网项。"""
        covered = set(self.placed_ids) | set(self.flagged_ids)
        return tuple(candidate_id for candidate_id in self.input_ids if candidate_id not in covered)

    @property
    def unknown_slot_ids(self) -> tuple[str, ...]:
        """槽位里出现了输入中不存在的候选 id（说明排列层凭空造了卡）。"""
        known = set(self.input_ids)
        return tuple(candidate_id for candidate_id in self.placed_ids if candidate_id not in known)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "input_candidate_ids": list(self.input_ids),
            "placed_candidate_ids": list(self.placed_ids),
            "flagged_candidate_ids": list(self.flagged_ids),
            "untraced_candidate_ids": list(self.untraced_ids),
            "validation_errors": list(self.validation_errors),
            "layout": self.layout.to_dict(),
        }


@dataclass(frozen=True)
class LayoutReport:
    """一组夹具的报告 + 验收对照结论。"""

    directory: Path
    options: LayoutOptions
    fixtures: tuple[FixtureLayout, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "reading_direction": self.options.reading_direction,
            "fixtures": [fixture.to_dict() for fixture in self.fixtures],
            "acceptance": [
                {"ok": ok, "criterion": criterion, "evidence": evidence}
                for ok, criterion, evidence in self.acceptance()
            ],
        }

    def by_tag(self, tags: Sequence[str]) -> list[FixtureLayout]:
        return [fixture for fixture in self.fixtures if any(tag in fixture.name for tag in tags)]

    def acceptance(self) -> list[tuple[bool, str, str]]:
        """Issue #11 的四条可观察验收，逐条给出「通过/未通过 + 依据」。"""
        results: list[tuple[bool, str, str]] = []

        structural = [
            (fixture, error) for fixture in self.fixtures for error in fixture.validation_errors
        ]
        untraced = [fixture for fixture in self.fixtures if fixture.untraced_ids]
        invented = [fixture for fixture in self.fixtures if fixture.unknown_slot_ids]
        ok = self.fixtures and not structural and not untraced and not invented
        detail = f"{len(self.fixtures)} 个夹具的 Layout 均通过 §4 校验"
        if structural:
            detail = "结构校验失败：" + "；".join(f"{f.name}: {e}" for f, e in structural)
        if untraced:
            detail += "；未追溯候选：" + "；".join(
                f"{fixture.name}: {', '.join(fixture.untraced_ids)}" for fixture in untraced
            )
        if invented:
            detail += "；槽位出现未知候选：" + "；".join(
                f"{fixture.name}: {', '.join(fixture.unknown_slot_ids)}" for fixture in invented
            )
        results.append((bool(ok), "输出 Layout（§4）且每条结论可追溯到候选 id", detail))

        two_row = self.by_tag(TWO_ROW_TAGS)
        if not two_row:
            results.append((False, "两行样例被正确分成两行", "没有两行类夹具（文件名需含 multi-row/two-row）"))
        else:
            bad = [fixture for fixture in two_row if len(fixture.layout.rows) != 2]
            evidence = "；".join(
                f"{fixture.name}: 行数 {len(fixture.layout.rows)}"
                f"（{', '.join('|'.join(row.candidate_ids) for row in fixture.layout.rows)}）"
                for fixture in two_row
            )
            results.append((not bad, "两行样例被正确分成两行", evidence))

        spacing = self.by_tag(SPACING_TAGS)
        if not spacing:
            results.append((False, "间距歧义样例产出 LB-L001", "没有间距歧义类夹具（文件名需含 spacing）"))
        else:
            bad = [fixture for fixture in spacing if DIAG_SPACING_AMBIGUOUS not in fixture.codes]
            evidence = "；".join(
                f"{fixture.name}: {DIAG_SPACING_AMBIGUOUS} × "
                f"{len(fixture.layout.diagnostics_with(DIAG_SPACING_AMBIGUOUS))}"
                for fixture in spacing
            )
            results.append((not bad, "间距歧义样例产出 LB-L001", evidence))

        duplicates = self.by_tag(DUPLICATE_TAGS)
        if not duplicates:
            results.append((False, "重复候选去重后有 LB-L002", "没有重复候选类夹具（文件名需含 duplicate）"))
        else:
            bad = [fixture for fixture in duplicates if DIAG_DUPLICATE_REMOVED not in fixture.codes]
            pieces: list[str] = []
            for fixture in duplicates:
                removed = [
                    candidate_id
                    for entry in fixture.layout.diagnostics_with(DIAG_DUPLICATE_REMOVED)
                    for candidate_id in entry.get("candidate_ids", [])
                    if candidate_id not in fixture.placed_ids
                ]
                pieces.append(
                    f"{fixture.name}: LB-L002 × "
                    f"{len(fixture.layout.diagnostics_with(DIAG_DUPLICATE_REMOVED))}，移除 "
                    f"{', '.join(removed) if removed else '无'}"
                )
            results.append((not bad, "重复候选去重后有 LB-L002", "；".join(pieces)))
        return results

    def format(self) -> str:
        lines: list[str] = [
            f"排列层自检：{self.directory}（reading_direction={self.options.reading_direction}，"
            f"pitch={'自适应' if self.options.pitch_px is None else f'{self.options.pitch_px:g}px'}）"
        ]
        for fixture in self.fixtures:
            counts = fixture.layout.basis.get("counts", {})
            lines.append(
                f"  {fixture.name:<18} 候选 {counts.get('input', len(fixture.input_ids))} → "
                f"行 {len(fixture.layout.rows)} / 槽位 {len(fixture.layout.slots)}"
                f"，去重移除 {counts.get('removed_duplicates', 0)}"
                f"，身份未知 {counts.get('excluded_unknown_identity', 0)}"
                f"，诊断 {len(fixture.layout.diagnostics)} 条"
            )
            for row in fixture.layout.rows:
                cells = " ".join(
                    f"{slot.candidate_id}({slot.card_face}@{slot.x_px},{slot.y_px})"
                    for slot in row.slots
                )
                lines.append(f"    行 {row.row_index}: {cells}")
            for entry in fixture.layout.diagnostics:
                ids = ",".join(entry.get("candidate_ids", []))
                suffix = f" [{ids}]" if ids else ""
                lines.append(
                    f"    诊断 {entry['code']} {entry['severity']}: {entry['message']}{suffix}"
                )
            if fixture.validation_errors:
                for error in fixture.validation_errors:
                    lines.append(f"    校验失败: {error}")

        lines.append("验收对照：")
        for ok, criterion, evidence in self.acceptance():
            lines.append(f"  [{'通过' if ok else '未通过'}] {criterion} —— {evidence}")

        total = sum(len(fixture.layout.slots) for fixture in self.fixtures)
        untraced = sum(len(fixture.untraced_ids) for fixture in self.fixtures)
        lines.append(
            f"合计 {len(self.fixtures)} 个候选夹具 / {total} 个槽位 / 未追溯候选 {untraced} 个"
        )
        return "\n".join(lines) + "\n"


def load_fixture(path: Path, *, options: LayoutOptions | None = None) -> FixtureLayout:
    """读一个 ``CardCandidateList`` 夹具并排列它（同时做 §4 结构校验）。"""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    candidates = candidates_from_document(document)
    layout = layout_from_document(document, options=options)
    try:
        validate_layout(layout.to_dict())
        errors: tuple[str, ...] = ()
    except ValueError as error:  # pragma: no cover - 只有实现出错才会走到
        errors = (str(error),)
    return FixtureLayout(
        name=Path(path).stem,
        path=Path(path),
        input_ids=tuple(candidate.candidate_id for candidate in candidates),
        layout=layout,
        validation_errors=errors,
    )


def build_report(
    directory: Path, *, options: LayoutOptions | None = None
) -> LayoutReport:
    """对一个目录下的全部 ``CardCandidateList`` 夹具跑排列（按文件名排序，结果确定）。"""
    root = Path(directory)
    fixtures: list[FixtureLayout] = []
    for path in sorted(root.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("kind") != "CardCandidateList":
            continue
        fixtures.append(load_fixture(path, options=options))
    return LayoutReport(
        directory=root,
        options=options or LayoutOptions(),
        fixtures=tuple(fixtures),
    )


__all__ = ["FixtureLayout", "LayoutReport", "build_report", "load_fixture"]
