"""S2 对象的协议细节：诊断码、JSON 组装与最小校验。

`docs/PROTOCOL.md` §3 只定义了 ``MarkerCandidateList`` / ``CardCandidateList`` 的字段，没有定义
诊断码，也没有给这两个对象留 ``diagnostics`` 槽位；而 §0 又要求「未知枚举值必须降级为
``unknown``，且必须在诊断中体现」，§9 给了诊断对象的统一形状。本模块按**加法式**（§10 规则 1）
补上这两处：两个 S2 对象都带一个可选 ``diagnostics`` 数组，诊断码用 ``LB-V0xx`` 区间。
这是 LB-003 发现的协议缺口之一，已在 ``docs/PROGRESS.md`` 与 PR 描述中列出，不改
``docs/PROTOCOL.md`` 本身（协议由仓库所有者维护）。

``validate_*`` 是**最小**校验：只核对 §3 定义过的字段、取值范围与枚举，不做 JSON Schema 级别的
完备校验（完整的 schema 工具留给 LB-006 / LB-009）。
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "0.0.1"

#: §3.2 的 ``status`` 取值。
CARD_STATUSES = frozenset({"ok", "unknown_card", "occluded", "clipped", "ambiguous"})

#: 本实现新增的识别层诊断码（§9 的 ``LB-V0xx`` 区间；``LB-V002`` 沿用 §9 的示例语义）。
DIAG_UNKNOWN_CARD = "LB-V001"  # 标记在字典内但未在卡片字典登记（status=unknown_card）
DIAG_MARKER_CLIPPED = "LB-V002"  # 标记被画面边界裁切（§9 示例沿用）
DIAG_UNREADABLE = "LB-V003"  # 检出标记形状但码字不可靠（遮挡或噪声）
DIAG_DUPLICATE = "LB-V004"  # 同一标记被重复检出，已按置信度保留一个
DIAG_AMBIGUOUS = "LB-V005"  # 码字在多个标记之间无法区分
DIAG_CARD_CLIPPED = "LB-V006"  # 由标记外推的卡片范围越出画面

SEVERITIES = frozenset({"info", "warning", "error"})

#: ``schema_version`` 的容忍形状（``major.minor`` 或 ``major.minor.patch``）。
VERSION_PATTERN = re.compile(r"\d+\.\d+(\.\d+)?")


def diagnostic(
    code: str,
    severity: str,
    message: str,
    *,
    candidate_ids: list[str] | None = None,
) -> dict[str, Any]:
    """按 §9 的统一形状构造一个诊断对象。"""
    if severity not in SEVERITIES:
        raise ValueError(f"severity 必须属于 {sorted(SEVERITIES)}：{severity!r}")
    entry: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if candidate_ids:
        entry["candidate_ids"] = list(candidate_ids)
    return entry


def _require(document: dict[str, Any], key: str, kind: str) -> Any:
    if key not in document:
        raise ValueError(f"{kind} 缺少字段 {key!r}")
    return document[key]


def validate_diagnostics(entries: Any) -> None:
    """校验 §9 的诊断数组形状（``code`` / ``severity`` / ``message``，可选 ``candidate_ids``）。

    S2 与 S3 的校验都复用这一份；``diagnostics`` 是 LB-003 起的**加法式**扩展（§10 规则 1），
    协议 §4 的示例里已经出现 ``diagnostics``，但没定义它的校验口径。
    """
    if not isinstance(entries, list):
        raise ValueError("diagnostics 必须是数组")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("diagnostics 的每一项必须是对象")
        for key in ("code", "severity", "message"):
            if key not in entry:
                raise ValueError(f"诊断对象缺少字段 {key!r}")
        if entry["severity"] not in SEVERITIES:
            raise ValueError(f"诊断 severity 非法：{entry['severity']!r}")
        if "candidate_ids" in entry:
            ids = entry["candidate_ids"]
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError("诊断的 candidate_ids 必须是字符串数组")


def _check_common(document: dict[str, Any], kind: str) -> None:
    if document.get("kind") != kind:
        raise ValueError(f"kind 必须是 {kind!r}，实际 {document.get('kind')!r}")
    version = document.get("schema_version")
    # 协议 §0 写的是 `major.minor`，但 §2~§7 的全部示例都写成三段式（``0.0.1``）。
    # 这里两种都接受，并把「§0 与示例不一致」记进 LB-003 的协议缺口清单。
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"schema_version 必须是 'major.minor[.patch]' 字符串，实际 {version!r}")
    if not isinstance(document.get("frame_id"), str):
        raise ValueError("frame_id 必须是字符串")
    validate_diagnostics(document.get("diagnostics", []))


def _check_point(value: Any, name: str) -> None:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{name} 必须是长度为 2 的数组")
    if not all(isinstance(v, int) for v in value):
        raise ValueError(f"{name} 必须是整数像素坐标（§0），实际 {value!r}")


def _check_confidence(value: Any, name: str) -> None:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须是数值")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} 必须落在 [0,1]，实际 {value!r}")


def validate_marker_candidate_list(document: dict[str, Any]) -> None:
    """校验 ``MarkerCandidateList``（§3.1 + 本实现的加法式扩展）。"""
    _check_common(document, "MarkerCandidateList")
    if not isinstance(_require(document, "dictionary", "MarkerCandidateList"), str):
        raise ValueError("dictionary 必须是字符串")
    markers = _require(document, "markers", "MarkerCandidateList")
    if not isinstance(markers, list):
        raise ValueError("markers 必须是数组")
    for marker in markers:
        if not isinstance(marker, dict):
            raise ValueError("markers 的每一项必须是对象")
        marker_id = _require(marker, "marker_id", "marker")
        if not isinstance(marker_id, str):
            raise ValueError(f"marker_id 必须是字符串（§0），实际 {marker_id!r}")
        corners = _require(marker, "corners_px", "marker")
        if not isinstance(corners, list) or len(corners) != 4:
            raise ValueError("corners_px 必须是 4 个点")
        for point in corners:
            _check_point(point, "corners_px[]")
        _check_point(_require(marker, "center_px", "marker"), "center_px")
        angle = _require(marker, "angle_deg", "marker")
        if not isinstance(angle, (int, float)):
            raise ValueError("angle_deg 必须是数值")
        _check_confidence(_require(marker, "confidence", "marker"), "confidence")
        if "pose" in marker and marker["pose"] is not None:
            pose = marker["pose"]
            for key in ("x_mm", "y_mm", "z_mm", "yaw_deg"):
                if key not in pose or not isinstance(pose[key], (int, float)):
                    raise ValueError(f"pose 缺少数值字段 {key!r}")


def validate_card_candidate_list(document: dict[str, Any]) -> None:
    """校验 ``CardCandidateList``（§3.2 + 本实现的加法式扩展）。"""
    _check_common(document, "CardCandidateList")
    cards = _require(document, "cards", "CardCandidateList")
    if not isinstance(cards, list):
        raise ValueError("cards 必须是数组")
    seen_ids: set[str] = set()
    for card in cards:
        if not isinstance(card, dict):
            raise ValueError("cards 的每一项必须是对象")
        candidate_id = _require(card, "candidate_id", "card")
        if not isinstance(candidate_id, str):
            raise ValueError("candidate_id 必须是字符串")
        if candidate_id in seen_ids:
            raise ValueError(f"candidate_id 重复：{candidate_id}")
        seen_ids.add(candidate_id)
        marker_ids = _require(card, "marker_ids", "card")
        if not isinstance(marker_ids, list) or not all(isinstance(m, str) for m in marker_ids):
            raise ValueError("marker_ids 必须是字符串数组")
        for key in ("card_type", "card_face"):
            if not isinstance(_require(card, key, "card"), str):
                raise ValueError(f"{key} 必须是字符串")
        if "orientation_quadrant" in card:
            quadrant = card["orientation_quadrant"]
            if not isinstance(quadrant, int) or not 0 <= quadrant <= 3:
                raise ValueError(f"orientation_quadrant 必须是 0..3 的整数，实际 {quadrant!r}")
        else:
            # 方向未知的三种情况：身份不可读（occluded / ambiguous）、或标记未登记（unknown_card，
            # 此时没有「卡片相对标记」的注册关系可用）。其余状态必须给出朝向。
            if card["status"] not in {"occluded", "ambiguous", "unknown_card"}:
                raise ValueError(
                    "只有 occluded / ambiguous / unknown_card 候选允许缺 "
                    "orientation_quadrant（方向未知）"
                )
        _check_point(_require(card, "center_px", "card"), "center_px")
        _check_confidence(_require(card, "confidence", "card"), "confidence")
        status = _require(card, "status", "card")
        if status not in CARD_STATUSES:
            raise ValueError(f"status 必须是 {sorted(CARD_STATUSES)} 之一，实际 {status!r}")
