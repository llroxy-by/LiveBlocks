"""一屏渲染：把一份运行记录 + 回放位置画成「分层可视化」的调试视图。

输出三种形态，**全部由同一份 ``View`` 数据模型渲染**，因此三者天然一致：

- ``render_html``：单屏 HTML（自包含：内联 CSS、叠加图以 base64 data URI 内嵌，
  没有 ``<script>``、没有外链、没有时间戳），用任意浏览器打开即可，也是截图证据的来源；
- ``render_text``：等宽文本视图，给终端与 CI 用（不依赖浏览器）；
- ``render_overlay``：帧叠加面板的 PNG（灰度，仓库自带的编码器，字节可复现）。

界面本身**只读**：不改记录、不改任何业务数据（``docs/ARCHITECTURE.md`` §3.5）。
同一份记录 + 同一个 ``step`` 渲染两次，三种形态都逐字节相等。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from .protocol import (
    ISSUE_BAD_FRAME,
    LAYER_ORDER,
    LAYER_TITLES,
    TIMING_LABELS,
    TIMING_LAYERS,
    UI_VERSION,
    RecordIssue,
    format_ms,
    issue,
)
from .raster import Canvas, INK, INK_SOFT, load_overlay_font, placeholder
from .record import RunRecord, parse_record_file, resolve_frame_path
from .replay import Replay, StepState, build_replay

#: 帧图像缺失时占位图的尺寸（与 LB-002 的合成帧一致，便于对照）。
PLACEHOLDER_WIDTH = 640
PLACEHOLDER_HEIGHT = 480

#: 事件表里 ``detail`` 的显示宽度（超长截断，避免一屏被一条事件撑爆）。
DETAIL_WIDTH = 72


# --- 帧叠加面板 ---------------------------------------------------------------


def render_overlay(
    record: RunRecord,
    *,
    record_path: str | Path | None = None,
) -> tuple[bytes, tuple[RecordIssue, ...]]:
    """画「帧叠加」面板：原帧 + 标记框 + 卡片中心 + 排列槽位。返回 ``(PNG, 问题)``。"""
    font = load_overlay_font()
    problems: list[RecordIssue] = []
    frame = record.frame
    image_ref = frame.get("image_ref")
    canvas: Canvas
    resolved = resolve_frame_path(image_ref, record_path=record_path or record.source or None)
    if resolved is None:
        canvas = placeholder(
            PLACEHOLDER_WIDTH,
            PLACEHOLDER_HEIGHT,
            [
                "frame image unavailable",
                f"frame_id={record.frame_id or '-'}",
                f"image_ref={image_ref or '-'}",
                "LB-U007: overlay shows markers/slots only",
            ],
            font=font,
        )
        problems.append(
            issue(
                ISSUE_BAD_FRAME,
                "error",
                f"引用的帧图像找不到（image_ref={image_ref!r}）：叠加面板显示占位图",
                layer="vision",
            )
        )
    else:
        try:
            canvas = Canvas.from_png(resolved.read_bytes())
        except (OSError, ValueError) as exc:
            canvas = placeholder(
                PLACEHOLDER_WIDTH,
                PLACEHOLDER_HEIGHT,
                ["frame image unreadable", f"image_ref={image_ref or '-'}", f"LB-U007: {exc}"[:64]],
                font=font,
            )
            problems.append(
                issue(
                    ISSUE_BAD_FRAME,
                    "error",
                    f"帧图像读不出来（{resolved}）：{exc}；叠加面板显示占位图",
                    layer="vision",
                )
            )
        else:
            expected = (frame.get("width"), frame.get("height"))
            if all(isinstance(value, int) for value in expected) and expected != (
                canvas.width,
                canvas.height,
            ):
                problems.append(
                    issue(
                        ISSUE_BAD_FRAME,
                        "warning",
                        f"RunHeader.frame 声明尺寸 {expected[0]}x{expected[1]}，"
                        f"实际图像是 {canvas.width}x{canvas.height}：按实际尺寸叠加",
                        layer="vision",
                    )
                )

    # 标记框（§3.1 的 corners_px，顺序为标记自身左上/右上/右下/左下）
    for marker in record.markers:
        corners = marker.get("corners") or []
        if len(corners) == 4:
            canvas.draw_polyline(list(corners), closed=True, shade=INK, thickness=1)
            anchor = corners[0]
            canvas.draw_label(
                f"m{marker.get('marker_id') or '?'}", (anchor[0] + 2, max(0, anchor[1] - 12)), font=font
            )
        center = marker.get("center")
        if center is not None:
            canvas.set(center[0], center[1], INK)

    # 卡片候选（§3.2 的 center_px；标签给 candidate_id / 卡面 / 状态）
    for card in record.cards:
        cx, cy = card["center"]
        canvas.draw_cross(cx, cy, size=6, shade=INK)
        confidence = card.get("confidence")
        suffix = "" if confidence is None else f" {confidence:.2f}"
        canvas.draw_label(
            f"{card['candidate_id']} {card['card_face']} {card['status']}{suffix}",
            (cx + 8, cy - 20),
            font=font,
        )

    # 排列槽位（§4：行/槽位 + 画面坐标）
    for row in record.rows:
        for slot in row.get("slots") or []:
            x, y = slot.get("x_px"), slot.get("y_px")
            if not isinstance(x, int) or not isinstance(y, int):
                continue
            canvas.draw_polyline(
                [(x - 7, y - 7), (x + 7, y - 7), (x + 7, y + 7), (x - 7, y + 7)],
                closed=True,
                shade=INK_SOFT,
                thickness=1,
            )
            canvas.draw_label(f"r{row['row_index']}s{slot['slot_index']}", (x + 9, y + 2), font=font)

    # 图例（左上角），顺带把「这是模拟/回放数据」写在图里
    legend = [
        f"LB-008 overlay v{UI_VERSION} backend={record.backend or '?'} frame={record.frame_id or '-'}",
        f"markers={len(record.markers)} cards={len(record.cards)} slots={_slot_count(record)}",
        "quad=marker cross=card box=slot",
    ]
    canvas.fill_rect(4, 4, 4 + 8 * max(len(line) for line in legend) + 8, 4 + 14 * len(legend) + 4, 16)
    for index, line in enumerate(legend):
        canvas.draw_label(line, (8, 8 + index * 14), font=font, background=None)

    return canvas.to_png(), tuple(problems)


def _slot_count(record: RunRecord) -> int:
    return sum(len(row.get("slots") or []) for row in record.rows)


def _card_status_counts(record: RunRecord) -> dict[str, int]:
    counts: dict[str, int] = {}
    for card in record.cards:
        status = card.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


# --- 视图数据模型 -------------------------------------------------------------


@dataclass
class View:
    """一屏视图的数据模型：记录 + 回放位置 + 叠加图 + 问题清单。"""

    record: RunRecord
    replay: Replay
    step: int
    state: StepState
    overlay_png: bytes
    record_path: str = ""
    extra_issues: tuple[RecordIssue, ...] = field(default_factory=tuple)

    @property
    def issues(self) -> tuple[RecordIssue, ...]:
        return tuple(self.record.issues) + tuple(self.extra_issues)

    def issues_for(self, layer: str) -> tuple[RecordIssue, ...]:
        return tuple(entry for entry in self.issues if entry.layer == layer)

    @property
    def severity_counts(self) -> dict[str, int]:
        counts = {"error": 0, "warning": 0, "info": 0}
        for entry in self.issues:
            counts[entry.severity] = counts.get(entry.severity, 0) + 1
        return counts

    @property
    def layer_present(self) -> dict[str, bool]:
        present = {layer: False for layer in LAYER_ORDER}
        if self.record.cards or self.record.markers:
            present["vision"] = True
        present["layout"] = bool(self.record.rows) or "Layout" in self.record.documents
        present["ast"] = bool(self.record.ast_root)
        present["ir"] = "Ir" in self.record.documents
        present["exec"] = "ExecResult" in self.record.documents or bool(self.record.events)
        return present

    @property
    def complete_layers(self) -> tuple[str, ...]:
        return tuple(layer for layer in LAYER_ORDER if self.layer_present[layer])

    def overlay_data_uri(self) -> str:
        encoded = base64.b64encode(self.overlay_png).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    # --- 文本视图 ---------------------------------------------------------

    def render_text(self) -> str:
        lines: list[str] = []
        header = self.record.header or {}
        lines.append("=" * 96)
        lines.append(f"序界 LiveBlocks — LB-008 调试视图（ui v{UI_VERSION}）  只读回放")
        lines.append("=" * 96)
        lines.append(
            f"记录：{self.record_path or '?'}   行数：{self.record.lines}   "
            f"run_id：{self.record.run_id or '?'}   backend：{self.record.backend or '?'}"
        )
        lines.append(
            f"帧：{self.record.frame_id or '-'}（{(self.record.frame or {}).get('image_ref') or '无 image_ref'}）   "
            f"单步：{self.step}/{self.replay.total_steps}"
        )
        lines.append(f"五层：{' / '.join(title for title in (LAYER_TITLES[layer] for layer in self.complete_layers)) or '（无）'}")
        lines.append("")
        lines.append("--- 问题提示 ---")
        if not self.issues:
            lines.append("（无：记录里的字段齐全）")
        else:
            counts = self.severity_counts
            lines.append(
                f"共 {len(self.issues)} 条（error {counts['error']} / warning {counts['warning']} / info {counts['info']}）"
            )
            for entry in self.issues:
                layer = f" @{entry.layer}" if entry.layer else ""
                lines.append(f"  {entry.describe()}{layer}")
        for layer in LAYER_ORDER:
            lines.append("")
            lines.append(f"--- {LAYER_TITLES[layer]} ---")
            if not self.layer_present[layer]:
                lines.append("  [缺失] 这一层没有出现在记录里（LB-U004），下面是占位")
            lines.extend(self._text_layer(layer))
            for entry in self.issues_for(layer):
                lines.append(f"  ! {entry.describe()}")
            for diagnostic in self.record.layer_diagnostics(layer):
                lines.append(f"  · {diagnostic.describe()}")
        lines.append("")
        lines.append("--- 回放（单步）---")
        lines.append(f"  当前：{self.state.describe()}")
        lines.append(f"  总事件：{len(self.record.events)}   已走完的步：{', '.join(self.state.steps_done) or '（无）'}")
        for note in self.replay.notes:
            lines.append(f"  ! {note}")
        lines.append("")
        return "\n".join(lines)

    def _text_layer(self, layer: str) -> list[str]:
        if layer == "vision":
            counts = _card_status_counts(self.record)
            return [
                f"  标记候选：{len(self.record.markers)}   卡片候选：{len(self.record.cards)}"
                f"（{' '.join(f'{key}={value}' for key, value in sorted(counts.items())) or '无'}）",
                "  叠加图：见 HTML/PNG 形态（文本形态只列数量）",
            ]
        if layer == "layout":
            lines = []
            for row in self.record.rows:
                slots = " | ".join(
                    f"[{slot['slot_index']}] {slot['card_face']} ({slot['candidate_id']})"
                    for slot in row.get("slots") or []
                )
                lines.append(f"  行 {row['row_index']}：{slots or '（空）'}")
            if not self.record.rows:
                lines.append("  （记录里 rows 为空或缺失）")
            return lines
        if layer == "ast":
            return [f"  {line}" for line in _ast_lines(self.record.ast_root)]
        if layer == "ir":
            lines = [
                f"  limits：max_steps={self.record.limits.get('max_steps', '?')} "
                f"timeout_ms={self.record.limits.get('timeout_ms', '?')}",
            ]
            for step in self.record.steps:
                marker = "→" if step["step_id"] == self.state.current_step_id else " "
                lines.append(
                    f" {marker} {step['step_id']:<6} {step['op']:<14} {_compact(step['args'], 40)}"
                )
            if not self.record.steps:
                lines.append("  （记录里 steps 为空或缺失）")
            return lines
        if layer == "exec":
            lines = [f"  世界状态：{self.state.world_text}"]
            for event in self.record.events:
                current = "→" if self.state.event is not None and event["seq"] == self.state.event["seq"] else " "
                lines.append(
                    f" {current} #{event['seq']:<3} {format_ms(event['at_ms']):>8} ms "
                    f"{event['phase']:<8} {event['step_id'] or '-':<6} {_compact(event['detail'], 48)}"
                )
            if not self.record.events:
                lines.append("  （记录里没有 ExecEvent）")
            if self.record.result:
                lines.append(
                    f"  收尾：status={self.record.result['status']} "
                    f"steps={self.record.result['steps_executed']} "
                    f"duration_ms={format_ms(self.record.result['duration_ms'])}"
                )
            lines.append("  分层耗时：")
            for key in TIMING_LAYERS:
                value = self.record.timings.get(key)
                shown = "记录未提供" if value is None else f"{format_ms(value)} ms"
                lines.append(f"    {TIMING_LABELS[key]:<16} {shown}")
            return lines
        return []

    # --- HTML 视图 --------------------------------------------------------

    def render_html(self) -> str:
        counts = self.severity_counts
        parts: list[str] = []
        parts.append("<!DOCTYPE html>")
        parts.append('<html lang="zh-CN"><head><meta charset="utf-8">')
        parts.append(f"<title>LB-008 调试视图 · {escape(self.record.run_id or 'unknown')}</title>")
        parts.append(f"<style>{_CSS}</style></head><body>")
        parts.append('<header class="topbar">')
        parts.append(f'<div class="brand">序界 LiveBlocks · 调试视图 <span class="ver">ui v{UI_VERSION}</span></div>')
        parts.append(
            '<div class="meta">'
            f'<span>记录 <code>{escape(self.record_path or "?")}</code></span>'
            f'<span>run_id <code>{escape(self.record.run_id or "?")}</code></span>'
            f'<span>backend <code>{escape(self.record.backend or "?")}</code></span>'
            f'<span>帧 <code>{escape(self.record.frame_id or "-")}</code></span>'
            f'<span>单步 <b>{self.step}</b>/<b>{self.replay.total_steps}</b></span>'
            "</div>"
        )
        parts.append(
            '<div class="layers-on-screen">本屏呈现：'
            + " · ".join(
                escape(LAYER_TITLES[layer]) if self.layer_present[layer] else f'<s>{escape(LAYER_TITLES[layer])}</s>'
                for layer in LAYER_ORDER
            )
            + "</div>"
        )
        parts.append("</header>")

        if self.issues:
            parts.append('<section class="banner" id="warnings">')
            parts.append(
                f'<div class="banner-title">记录有 {len(self.issues)} 条提示：'
                f'<b class="err">{counts["error"]} error</b> · '
                f'<b class="warn">{counts["warning"]} warning</b> · '
                f"<b>{counts['info']} info</b>（界面照常渲染，缺的就显示占位，不崩溃）</div>"
            )
            parts.append('<ul class="issues">')
            for entry in self.issues:
                layer = f'<span class="tag">{escape(entry.layer)}</span>' if entry.layer else ""
                where = f'<span class="tag">第 {entry.line} 行</span>' if entry.line else ""
                parts.append(
                    f'<li class="{escape(entry.severity)}"><code>{escape(entry.code)}</code>'
                    f'<span class="tag">{escape(entry.severity)}</span>{where}{layer}'
                    f"<span>{escape(entry.message)}</span></li>"
                )
            parts.append("</ul></section>")
        else:
            parts.append(
                '<section class="banner ok" id="warnings"><div class="banner-title">'
                "记录字段齐全：五层都读到了，没有 LB-U0xx 提示</div></section>"
            )

        parts.append('<main class="grid">')
        parts.append(self._html_vision())
        parts.append(self._html_layout())
        parts.append(self._html_ast())
        parts.append(self._html_ir())
        parts.append(self._html_exec())
        parts.append("</main>")
        parts.append(
            '<footer class="foot">只读视图：界面不改记录、不改业务数据。'
            "耗时来自记录里的 <code>RunHeader.timings_ms</code>（记录时实测，不是本屏渲染耗时）。"
            "事件数据来自模拟后端时 backend=sim：模拟通过 ≠ 硬件验证。</footer>"
        )
        parts.append("</body></html>")
        return "\n".join(parts) + "\n"

    def _panel_open(self, layer: str) -> str:
        title = escape(LAYER_TITLES[layer])
        missing = "" if self.layer_present[layer] else '<span class="tag bad">缺失</span>'
        return (
            f'<section class="panel layer-{layer}" data-layer="{layer}" id="layer-{layer}">'
            f'<h2>{title}{missing}</h2>'
        )

    def _panel_issues(self, layer: str) -> str:
        items = list(self.issues_for(layer))
        diagnostics = list(self.record.layer_diagnostics(layer))
        if not items and not diagnostics:
            return ""
        out = ['<div class="panel-notes">']
        for entry in items:
            out.append(f'<div class="note {escape(entry.severity)}"><code>{escape(entry.code)}</code> {escape(entry.message)}</div>')
        for diagnostic in diagnostics:
            out.append(
                f'<div class="note local"><code>{escape(diagnostic.code)}</code> '
                f'<span class="tag">{escape(diagnostic.severity)}</span> {escape(diagnostic.message)}</div>'
            )
        out.append("</div>")
        return "".join(out)

    def _html_vision(self) -> str:
        counts = _card_status_counts(self.record)
        out = [self._panel_open("vision")]
        out.append(f'<img class="frame" alt="frame overlay" src="{self.overlay_data_uri()}">')
        out.append(
            '<div class="statline">'
            f"标记候选 <b>{len(self.record.markers)}</b> · "
            f"卡片候选 <b>{len(self.record.cards)}</b> · "
            "状态 "
            + (" ".join(f'<span class="tag">{escape(k)}={v}</span>' for k, v in sorted(counts.items())) or "（无）")
            + "</div>"
        )
        out.append(
            '<div class="hint">框=标记（§3.1 corners_px） 十=卡片中心 方框=rXsY 槽位；'
            f"帧来源 <code>{escape(str((self.record.frame or {}).get('source') or 'unknown'))}</code></div>"
        )
        out.append(self._panel_issues("vision"))
        out.append("</section>")
        return "".join(out)

    def _html_layout(self) -> str:
        out = [self._panel_open("layout")]
        if not self.record.rows:
            out.append('<div class="empty">这一层没有可显示的行/槽位</div>')
        for row in self.record.rows:
            out.append(f'<div class="rowhead">行 {escape(str(row["row_index"]))}</div>')
            out.append('<table class="slots"><thead><tr><th>槽</th><th>卡面</th><th>候选</th><th>坐标</th></tr></thead><tbody>')
            for slot in row.get("slots") or []:
                coords = (
                    f'({slot["x_px"]}, {slot["y_px"]})'
                    if isinstance(slot.get("x_px"), int) and isinstance(slot.get("y_px"), int)
                    else "—"
                )
                confidence = slot.get("confidence")
                conf = "" if confidence is None else f' <span class="tag">{confidence:.2f}</span>'
                out.append(
                    f'<tr><td>{escape(str(slot["slot_index"]))}</td>'
                    f'<td><code>{escape(str(slot["card_face"]))}</code>{conf}</td>'
                    f'<td>{escape(str(slot["candidate_id"]))}</td><td>{escape(coords)}</td></tr>'
                )
            out.append("</tbody></table>")
        out.append(self._panel_issues("layout"))
        out.append("</section>")
        return "".join(out)

    def _html_ast(self) -> str:
        out = [self._panel_open("ast")]
        if not self.record.ast_root:
            out.append('<div class="empty">这一层没有可显示的 AST 根节点</div>')
        else:
            out.append('<ul class="tree">' + _ast_html(self.record.ast_root) + "</ul>")
        out.append(self._panel_issues("ast"))
        out.append("</section>")
        return "".join(out)

    def _html_ir(self) -> str:
        out = [self._panel_open("ir")]
        out.append(
            '<div class="statline">limits '
            f"max_steps=<b>{escape(str(self.record.limits.get('max_steps', '?')))}</b> "
            f"timeout_ms=<b>{escape(str(self.record.limits.get('timeout_ms', '?')))}</b> · "
            f"步数 <b>{len(self.record.steps)}</b></div>"
        )
        if not self.record.steps:
            out.append('<div class="empty">这一层没有可显示的步骤</div>')
        else:
            out.append('<table class="steps"><thead><tr><th>step</th><th>op</th><th>args</th><th>source</th></tr></thead><tbody>')
            for step in self.record.steps:
                current = (
                    ' class="current"'
                    if step["step_id"] == self.state.current_step_id and self.state.current_step_id
                    else ""
                )
                out.append(
                    f"<tr{current}><td><code>{escape(step['step_id'])}</code></td>"
                    f"<td><code>{escape(step['op'])}</code></td>"
                    f"<td>{escape(_compact(step['args'], 48))}</td>"
                    f"<td>{escape(_compact(step['source'], 24))}</td></tr>"
                )
            out.append("</tbody></table>")
        out.append(self._panel_issues("ir"))
        out.append("</section>")
        return "".join(out)

    def _html_exec(self) -> str:
        out = [self._panel_open("exec")]
        out.append(
            '<div class="stepbar">单步 '
            f"<b>{self.step}</b> / <b>{self.replay.total_steps}</b>"
            ' <span class="hint">（命令行 <code>liveblocks ui view --record … --step N</code> 可单步）</span></div>'
        )
        out.append(f'<div class="world">世界状态：{escape(self.state.world_text)}</div>')
        if self.state.event is not None:
            event = self.state.event
            out.append(
                f'<div class="world">当前事件：<code>#{escape(str(event["seq"]))} '
                f'{escape(str(event["phase"]))} {escape(str(event["step_id"] or "-"))}</code> '
                f'at_ms={escape(format_ms(event["at_ms"]))}</div>'
            )
        out.append(f'<div class="world">已走完的步：{escape(", ".join(self.state.steps_done) or "（无）")}</div>')
        if self.record.result:
            result = self.record.result
            out.append(
                '<div class="result">收尾：'
                f"status=<b>{escape(str(result['status']))}</b> "
                f"steps=<b>{escape(str(result['steps_executed']))}</b> "
                f"duration_ms=<b>{escape(format_ms(float(result['duration_ms'])))}</b> "
                f"backend=<code>{escape(str(result['backend']))}</code></div>"
            )
        out.append('<table class="events"><thead><tr><th>#</th><th>at_ms</th><th>phase</th><th>step</th><th>detail</th></tr></thead><tbody>')
        for event in self.record.events:
            current = (
                ' class="current"'
                if self.state.event is not None and event["seq"] == self.state.event["seq"]
                else ""
            )
            out.append(
                f"<tr{current}><td>{escape(str(event['seq']))}</td>"
                f"<td>{escape(format_ms(event['at_ms']))}</td>"
                f"<td>{escape(str(event['phase']))}</td>"
                f"<td>{escape(str(event['step_id'] or '-'))}</td>"
                f"<td class=\"detail\">{escape(_compact(event['detail'], DETAIL_WIDTH))}</td></tr>"
            )
        if not self.record.events:
            out.append('<tr><td colspan="5" class="empty">记录里没有 ExecEvent</td></tr>')
        out.append("</tbody></table>")
        out.append('<div class="timings"><div class="rowhead">分层耗时（记录时实测，RunHeader.timings_ms）</div>')
        values = [self.record.timings.get(key) for key in TIMING_LAYERS]
        biggest = max([value for value in values if isinstance(value, float)] or [0.0])
        for key, value in zip(TIMING_LAYERS, values):
            if value is None:
                out.append(
                    f'<div class="timing"><span class="tname">{escape(TIMING_LABELS[key])}</span>'
                    '<span class="tbar"><span class="tfill missing" style="width:0"></span></span>'
                    '<span class="tvalue">记录未提供</span></div>'
                )
                continue
            ratio = 0.0 if biggest <= 0 else float(value) / biggest
            width = max(2.0, round(ratio * 100.0, 1))
            out.append(
                f'<div class="timing"><span class="tname">{escape(TIMING_LABELS[key])}</span>'
                f'<span class="tbar"><span class="tfill" style="width:{width}%"></span></span>'
                f'<span class="tvalue">{escape(format_ms(value))} ms</span></div>'
            )
        out.append("</div>")
        for note in self.replay.notes:
            out.append(f'<div class="note info">{escape(note)}</div>')
        out.append(self._panel_issues("exec"))
        out.append("</section>")
        return "".join(out)


def _ast_html(node: Any, depth: int = 0) -> str:
    """把 §5 的 AST 节点渲染成嵌套列表（字段缺失时也不抛异常）。"""
    if not isinstance(node, dict):
        return f'<li><span class="value">{escape(_compact(node, 40))}</span></li>'
    kind = node.get("node")
    label = escape(str(kind) if kind is not None else "?")
    span = node.get("span")
    bits = [f'<span class="node">{label}</span>']
    if isinstance(span, dict):
        bits.append(f'<span class="tag">span {escape(_span_text(span))}</span>')
    if isinstance(node.get("name"), str):
        bits.append(f'<span class="value">{escape(node["name"])}</span>')
    if "value" in node:
        bits.append(f'<span class="value">{escape(str(node["value"]))}</span>')
    origin = node.get("origin")
    if isinstance(origin, dict):
        bits.append(f'<span class="tag">origin {escape(_compact(origin, 32))}</span>')
    children: list[str] = []
    for key in ("body", "args", "items", "branches"):
        value = node.get(key)
        if isinstance(value, list):
            for child in value:
                children.append(_ast_html(child, depth + 1))
    for key, value in node.items():
        if key in {"node", "span", "origin", "name", "value", "body", "args", "items", "branches"}:
            continue
        if isinstance(value, dict) and isinstance(value.get("node"), str):
            children.append(_ast_html(value, depth + 1))
        elif isinstance(value, list) and value and all(isinstance(item, dict) and "node" in item for item in value):
            for child in value:
                children.append(_ast_html(child, depth + 1))
    inner = "".join(children)
    if inner:
        return f'<li>{"".join(bits)}<ul>{inner}</ul></li>'
    return f'<li>{"".join(bits)}</li>'


def _span_text(span: dict[str, Any]) -> str:
    if "row" in span:
        return f'{span.get("row")}:{span.get("slot_from")}-{span.get("slot_to")}'
    return f'{span.get("row_from")}..{span.get("row_to")}:{span.get("slot_from")}-{span.get("slot_to")}'


def _ast_lines(node: Any) -> list[str]:
    """文本形态的 AST 树。"""
    lines: list[str] = []

    def walk(value: Any, depth: int) -> None:
        if isinstance(value, dict) and "node" in value:
            span = value.get("span")
            extra = ""
            if isinstance(span, dict):
                extra += f" span={_span_text(span)}"
            if isinstance(value.get("name"), str):
                extra += f" name={value['name']}"
            if "value" in value:
                extra += f" value={value['value']}"
            lines.append("  " * depth + f"{value['node']}{extra}")
            for key in ("body", "args", "items", "branches"):
                for child in value.get(key) or []:
                    walk(child, depth + 1)
            return
        if isinstance(value, list):
            for child in value:
                walk(child, depth)

    walk(node, 1)
    return lines or ["  （空）"]


def _compact(value: Any, limit: int) -> str:
    """把 JSON 片段压成一行并截断（固定规则，便于字节比对）。"""
    if value is None or value == {} or value == []:
        return "—"
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: #10151c; color: #dde5ee;
  font: 12px/1.45 system-ui, "Noto Sans CJK SC", "Source Han Sans SC", sans-serif; }
code { font-family: ui-monospace, "DejaVu Sans Mono", monospace; font-size: 11px; color: #9fd3ff; }
.topbar { position: sticky; top: 0; background: #16202b; border-bottom: 1px solid #2b3b4d;
  padding: 8px 12px; display: flex; flex-direction: column; gap: 4px; z-index: 5; }
.brand { font-size: 15px; font-weight: 600; letter-spacing: .3px; }
.brand .ver { font-size: 11px; color: #7f95ab; font-weight: 400; margin-left: 6px; }
.meta { display: flex; flex-wrap: wrap; gap: 12px; color: #9fb0c2; }
.meta b { color: #ffe08a; }
.layers-on-screen { color: #86c9a0; font-size: 11px; }
.layers-on-screen s { color: #a05a5a; }
.banner { margin: 8px 12px 0; padding: 8px 10px; border-radius: 6px;
  background: #2a1d1d; border: 1px solid #6d3a3a; }
.banner.ok { background: #16281d; border-color: #33543f; }
.banner-title { font-weight: 600; }
.banner .err { color: #ff9b9b; } .banner .warn { color: #ffd479; }
ul.issues { margin: 6px 0 0; padding-left: 16px; }
ul.issues li { margin: 2px 0; display: flex; gap: 6px; align-items: baseline; flex-wrap: wrap; }
li.error > span { color: #ffc9c9; } li.warning > span { color: #ffe6b3; }
.tag { font-size: 10px; padding: 0 4px; border-radius: 3px; background: #24313f; color: #8fa9c2; }
.tag.bad { background: #5a2020; color: #ffbcbc; }
main.grid { display: grid; gap: 8px; padding: 8px 12px 12px;
  grid-template-columns: minmax(420px, 1.15fr) minmax(260px, .85fr) minmax(260px, .85fr); }
.panel { background: #16202b; border: 1px solid #26323f; border-radius: 6px; padding: 8px 10px;
  min-width: 0; }
.panel h2 { margin: 0 0 6px; font-size: 12.5px; color: #b7d7ff; font-weight: 600;
  display: flex; gap: 6px; align-items: center; }
.layer-exec { grid-column: 2 / -1; }
img.frame { width: 100%; height: auto; image-rendering: pixelated; border: 1px solid #2c3a49;
  background: #0d1116; border-radius: 3px; }
.statline { color: #a9bccf; margin: 6px 0 2px; }
.hint { color: #7f95ab; font-size: 11px; }
.rowhead { margin: 6px 0 3px; color: #ffd479; font-size: 11px; }
table { width: 100%; border-collapse: collapse; font-size: 11px; }
th { text-align: left; color: #7f95ab; font-weight: 500; border-bottom: 1px solid #2b3b4d; padding: 2px 4px; }
td { padding: 2px 4px; border-bottom: 1px solid #1d2733; vertical-align: top; }
tr.current td { background: #1f3a2b; }
td.detail { color: #9fb0c2; word-break: break-all; }
ul.tree { list-style: none; margin: 0; padding-left: 10px; }
ul.tree ul { list-style: none; padding-left: 12px; border-left: 1px dotted #2b3b4d; }
ul.tree li { margin: 1px 0; }
.node { color: #ffd479; } .value { color: #a8e6a3; }
.stepbar { color: #a9bccf; margin-bottom: 4px; }
.stepbar b { color: #ffe08a; }
.world, .result { color: #a9bccf; font-size: 11px; margin: 2px 0; }
.timings { margin-top: 8px; }
.timing { display: grid; grid-template-columns: 118px 1fr 84px; gap: 6px; align-items: center;
  font-size: 11px; margin: 2px 0; }
.tname { color: #a9bccf; }
.tbar { background: #1d2733; border-radius: 3px; height: 8px; overflow: hidden; display: block; }
.tfill { display: block; height: 100%; background: linear-gradient(90deg, #3d7fc1, #7fd1ff); }
.tfill.missing { background: #4a3630; }
.tvalue { color: #cfe3f7; text-align: right; }
.empty { color: #a05a5a; font-size: 11px; }
.panel-notes { margin-top: 6px; display: flex; flex-direction: column; gap: 2px; }
.note { font-size: 11px; color: #b7c7d8; }
.note.error { color: #ffc9c9; } .note.warning { color: #ffe6b3; } .note.local code { color: #ffb86b; }
.foot { color: #6f8298; font-size: 11px; padding: 0 12px 12px; }
"""


# --- 构造视图 -----------------------------------------------------------------


def build_view(
    record: RunRecord,
    *,
    step: int | None = None,
    record_path: str | Path | None = None,
) -> View:
    """由记录构造一屏视图（含回放位置与叠加图）。"""
    replay = build_replay(record)
    clamped = replay.clamp(step)
    overlay_png, overlay_issues = render_overlay(record, record_path=record_path)
    # 视图里只显示**文件名**：同一份记录无论从哪个工作目录打开，渲染结果都逐字节相同
    # （回放确定性的一部分；完整路径由命令行的 stdout 给出，不进视图）。
    shown = record_path if record_path is not None else record.source
    return View(
        record=record,
        replay=replay,
        step=clamped,
        state=replay.state_at(clamped),
        overlay_png=overlay_png,
        record_path=Path(shown).name if shown else "",
        extra_issues=overlay_issues,
    )


def build_view_from_file(path: str | Path, *, step: int | None = None) -> View:
    """读记录文件并构造视图（坏记录也返回视图，只是带问题提示）。"""
    record = parse_record_file(path)
    return build_view(record, step=step, record_path=path)


__all__ = [
    "View",
    "build_view",
    "build_view_from_file",
    "render_overlay",
    "PLACEHOLDER_WIDTH",
    "PLACEHOLDER_HEIGHT",
]
