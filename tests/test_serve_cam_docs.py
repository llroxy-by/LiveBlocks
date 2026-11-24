"""``docs/CAPTURE.md`` 与实现的一致性（文档缺口 + 反向验证 + 验收④的状态守门）。

与 LB-005~LB-009 的 ``*_docs`` 测试同一思路：入库文档必须能被机器核对，而且要**反证**
核对函数不是永真式——删掉一行文档后必须真的报缺口。

最后一条守门很关键：Issue #26 的验收④（手机上选后置摄像头对着实体卡）**只能人验**，
文档里必须留着「待人工验证」。如果有人把它改成「已通过」，这里会失败。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from liveblocks.serve_cam import ServeCamApp, ServeCamConfig, TestClient
from liveblocks.serve_cam.docs import (
    DOC_PATH,
    REQUIRED_COMMANDS,
    REQUIRED_KINDS,
    REQUIRED_ROUTES,
    capture_document_gaps,
)
from liveblocks.serve_cam.protocol import ERROR_CODES

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / DOC_PATH


@pytest.fixture()
def doc_copy(tmp_path: Path) -> Path:
    target = tmp_path / "CAPTURE.md"
    shutil.copy(DOC, target)
    return target


def test_document_exists_and_has_no_gaps() -> None:
    assert DOC.is_file()
    assert capture_document_gaps() == ()


def test_document_states_its_draft_version_and_boundaries() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "`capture_version` 0.0.1" in text
    assert "草案" in text
    assert "不做公网鉴权" in text
    assert "不改 `docs/PROTOCOL.md`" in text


@pytest.mark.parametrize("route", REQUIRED_ROUTES)
def test_document_lists_each_route(route: str) -> None:
    assert route in DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", REQUIRED_KINDS)
def test_document_lists_each_kind(kind: str) -> None:
    assert f"`{kind}`" in DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_document_lists_each_command(command: str) -> None:
    assert command in DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("code", sorted(ERROR_CODES))
def test_document_lists_each_error_code(code: str) -> None:
    assert f"| `{code}` | {ERROR_CODES[code]} |" in DOC.read_text(encoding="utf-8")


def test_document_keeps_acceptance_item_four_as_pending_human_verification() -> None:
    """验收④由人验：文档必须写「待人工验证」+ 人工步骤，且不得声称已通过。"""
    text = DOC.read_text(encoding="utf-8")
    assert "待人工验证" in text
    row = next(line for line in text.splitlines() if line.startswith("| ④"))
    assert "待人工验证" in row
    assert "已通过" not in row and "通过" not in row.replace("待人工验证", "")
    # 人工步骤必须写清楚怎么验（手机 + 后置摄像头 + 实体卡 + 看到卡片 id）
    section = text.split("## 6. 人工步骤", 1)[1].split("## 7.", 1)[0]
    for needle in ("后置", "实体卡", "单帧抓拍", "卡片 id", "refused"):
        assert needle in section, f"人工步骤缺少：{needle}"


def test_document_links_from_readme() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/CAPTURE.md" in readme


# --- 反向验证：删一行就必须报缺口 -----------------------------------------------------

def test_missing_document_is_reported(tmp_path: Path) -> None:
    gaps = capture_document_gaps(tmp_path / "nope.md")
    assert gaps and "缺少文档" in gaps[0]


def test_deleting_an_error_row_is_reported(doc_copy: Path) -> None:
    row = "| `LB-C003` | 图像解码失败 |"
    text = doc_copy.read_text(encoding="utf-8")
    assert row in text
    doc_copy.write_text(text.replace(row, ""), encoding="utf-8")
    gaps = capture_document_gaps(doc_copy)
    assert any("LB-C003" in gap for gap in gaps), gaps


def test_deleting_a_route_is_reported(doc_copy: Path) -> None:
    text = doc_copy.read_text(encoding="utf-8")
    doc_copy.write_text(text.replace("/api/capture", "/api/х"), encoding="utf-8")
    gaps = capture_document_gaps(doc_copy)
    assert any("/api/capture" in gap for gap in gaps), gaps


def test_deleting_a_kind_is_reported(doc_copy: Path) -> None:
    text = doc_copy.read_text(encoding="utf-8")
    doc_copy.write_text(text.replace("`FrameRecognitionResult`", "识别结果"), encoding="utf-8")
    gaps = capture_document_gaps(doc_copy)
    assert any("FrameRecognitionResult" in gap for gap in gaps), gaps


def test_deleting_a_command_is_reported(doc_copy: Path) -> None:
    text = doc_copy.read_text(encoding="utf-8")
    doc_copy.write_text(text.replace("liveblocks serve-cam serve", "起服务"), encoding="utf-8")
    gaps = capture_document_gaps(doc_copy)
    assert any("serve-cam serve" in gap for gap in gaps), gaps


def test_deleting_the_pending_human_verification_marker_is_reported(doc_copy: Path) -> None:
    text = doc_copy.read_text(encoding="utf-8")
    doc_copy.write_text(text.replace("待人工验证", ""), encoding="utf-8")
    gaps = capture_document_gaps(doc_copy)
    assert any("待人工验证" in gap for gap in gaps), gaps


# --- 文档里写的路由与实现里的路由表必须一致 -------------------------------------------

def test_documented_routes_match_the_implementation_table(tmp_path: Path) -> None:
    client = TestClient(ServeCamApp(ServeCamConfig(live_dir=tmp_path / "live")))
    routes = client.get("/api/info").json()["routes"]
    assert set(REQUIRED_ROUTES) <= set(routes.values())
    for value in routes.values():
        prefix = value.split("<")[0]
        assert prefix in DOC.read_text(encoding="utf-8"), f"实现的 {value} 没写进文档"
