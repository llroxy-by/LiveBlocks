"""LB-032 手绘降级版：8×8 网格必须能从文字反解回位表（手绘说明不是摆设）。

「说明」很容易写成一张好看的图却和位表对不上，所以这里把 `liveblocks cardsheet stencil` 的
输出**解析回去**，逐卡与 `aruco_dictionaries.json` 的码字比对；另外验证「抄错一个格」确实会
变成别的位串（反证这份核对不是恒真）。
"""

from __future__ import annotations

import pytest

from liveblocks.sheet.spec import CardSheetSpec
from liveblocks.sheet.stencil import (
    StencilBlock,
    format_stencil,
    grid_for_marker,
    marker_code,
    parse_stencil,
    parse_stencil_blocks,
    stencil_blocks,
)


@pytest.fixture(scope="module")
def spec() -> CardSheetSpec:
    return CardSheetSpec.build()


def test_every_card_grid_round_trips_to_the_dictionary_code(spec: CardSheetSpec) -> None:
    text = format_stencil(spec)
    codes = parse_stencil_blocks(text)
    assert len(codes) == len(spec.cards)
    for card in spec.cards:
        assert codes[card.card_id] == marker_code(spec.aruco, card.marker_id), card.card_id
        assert codes[card.card_id] == _expected_from_dictionary(spec, card.marker_id)


def _expected_from_dictionary(spec: CardSheetSpec, marker_id: int) -> str:
    """直接从位表拼出「含黑边」的 6×6 位串（不复用被测的 `marker_code`）。"""
    aruco = spec.aruco
    modules = aruco.marker_modules
    border = aruco.border_bits
    bits = aruco.code(marker_id)
    rows: list[str] = []
    for row in range(modules + 2 * border):
        line: list[str] = []
        for col in range(modules + 2 * border):
            inner_row, inner_col = row - border, col - border
            inside = 0 <= inner_row < modules and 0 <= inner_col < modules
            if not inside:
                line.append("0")
            else:
                line.append(bits[inner_row * modules + inner_col])
        rows.append("".join(line))
    return "".join(rows)


def test_grid_has_one_quiet_ring_and_one_border_ring(spec: CardSheetSpec) -> None:
    grid = grid_for_marker(spec.aruco, 0)
    assert len(grid) == 8 and all(len(row) == 8 for row in grid)
    # 最外圈是静区（空格）
    assert set(grid[0]) == {" "}
    assert set(grid[-1]) == {" "}
    assert all(row[0] == " " and row[-1] == " " for row in grid)
    # 往里一圈是黑边（全黑）
    assert set(grid[1][1:7]) == {"#"}
    assert set(grid[6][1:7]) == {"#"}
    assert all(row[1] == "#" and row[6] == "#" for row in grid[1:7])


def test_stencil_text_carries_the_instructions(spec: CardSheetSpec) -> None:
    block = stencil_blocks(spec)[0]
    text = block.text()
    assert isinstance(block, StencilBlock)
    assert "marker_id=" in text
    assert "每格 4 mm" in text
    assert "朝向 0°" in text
    assert "# = 涂黑" in text
    header = format_stencil(spec)
    assert header.count("marker_id=") == len(spec.cards)
    assert "手绘降级版" in header
    assert "recognize detect" in header


def test_stencil_can_be_filtered_and_is_deterministic(spec: CardSheetSpec) -> None:
    only = format_stencil(spec, ["C01", "N05"])
    codes = parse_stencil_blocks(only)
    assert set(codes) == {"C01", "N05"}
    assert only == format_stencil(spec, ["C01", "N05"])


def test_one_wrong_cell_changes_the_decoded_code(spec: CardSheetSpec) -> None:
    text = format_stencil(spec, ["C01"])
    tampered = text.replace("##..##", "####..", 1) if "##..##" in text else text.replace("##", "..", 1)
    assert tampered != text
    with pytest.raises(AssertionError):
        assert parse_stencil_blocks(tampered)["C01"] == marker_code(spec.aruco, 0)


@pytest.mark.parametrize("bad", ["", "| ## |\n" * 3, "  +--+\n  |  |\n"])
def test_parse_rejects_incomplete_grids(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_stencil(bad)


def test_marker_code_matches_the_bit_table(spec: CardSheetSpec) -> None:
    for card in spec.cards:
        code = marker_code(spec.aruco, card.marker_id)
        assert len(code) == spec.module_count * spec.module_count
        assert set(code) <= {"0", "1"}
        # 黑边必须是 0（涂黑）
        assert code[0] == "0" and code[-1] == "0"
