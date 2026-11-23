"""卡片字典与 ArUco 字典位表。

- **ArUco 字典**（``data/aruco_dictionaries.json``）：真正的 ArUco 码字位表，按
  「1 = 白模块、0 = 黑模块」存成字符串。取自 OpenCV 的 ``DICT_4X4_50``，入库是为了
  让渲染与编码不依赖 OpenCV（LB-003 识别时再用 OpenCV 检测同一批码字）。
- **卡片字典**（``data/card_dictionary.json``）：``card_id → marker_id + card_face``
  的登记表，是「卡片身份」的唯一来源。

两张表都在加载时做完整性校验（编号范围、唯一性、码字长度、旋转唯一性），避免生成出
「字典里查不到」或「转 90° 就撞码」的卡片。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data import read_json


def _rotations(code: str, modules: int) -> frozenset[str]:
    grid = [list(code[r * modules : (r + 1) * modules]) for r in range(modules)]
    seen: set[str] = set()
    for _ in range(4):
        seen.add("".join("".join(row) for row in grid))
        grid = [list(row) for row in zip(*grid[::-1])]  # 顺时针 90°
    return frozenset(seen)


@dataclass(frozen=True)
class ArucoDictionary:
    """一个 ArUco 字典：``codes[i]`` 是 ``marker_id == i`` 的位串。"""

    name: str
    marker_modules: int
    border_bits: int
    codes: tuple[str, ...]
    source: str = ""

    @property
    def max_marker_id(self) -> int:
        return len(self.codes) - 1

    def code(self, marker_id: int) -> str:
        if not 0 <= marker_id <= self.max_marker_id:
            raise KeyError(f"marker_id {marker_id} 不在 {self.name} 的编号范围 0..{self.max_marker_id} 内")
        return self.codes[marker_id]

    def matrix(self, marker_id: int) -> tuple[tuple[str, ...], ...]:
        """返回 ``marker_modules x marker_modules`` 的位矩阵，``"1"`` 为白模块。"""
        code = self.code(marker_id)
        n = self.marker_modules
        return tuple(tuple(code[r * n : (r + 1) * n]) for r in range(n))

    def validate(self) -> None:
        n = self.marker_modules
        if n <= 0:
            raise ValueError(f"{self.name}: marker_modules 必须为正")
        if self.border_bits < 1:
            raise ValueError(f"{self.name}: border_bits 至少为 1（ArUco 标记必须有黑边）")
        seen_rotations: dict[frozenset[str], int] = {}
        for marker_id, code in enumerate(self.codes):
            if len(code) != n * n:
                raise ValueError(
                    f"{self.name}: 码字 {marker_id} 长度 {len(code)} 与 {n}x{n} 不符"
                )
            if set(code) - {"0", "1"}:
                raise ValueError(f"{self.name}: 码字 {marker_id} 含非 0/1 字符")
            rotations = _rotations(code, n)
            for rotation in rotations:
                if rotation in seen_rotations and seen_rotations[rotation] != marker_id:
                    raise ValueError(
                        f"{self.name}: 码字 {marker_id} 与 {seen_rotations[rotation]} 旋转后撞码"
                    )
                seen_rotations[rotation] = marker_id


@dataclass(frozen=True)
class CardEntry:
    card_id: str
    marker_id: int
    card_type: str
    card_face: str
    label: str

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "marker_id": self.marker_id,
            "card_type": self.card_type,
            "card_face": self.card_face,
            "label": self.label,
        }


@dataclass(frozen=True)
class CardDictionary:
    version: str
    aruco_dictionary: str
    cards: tuple[CardEntry, ...]

    @property
    def by_id(self) -> dict[str, CardEntry]:
        return {card.card_id: card for card in self.cards}

    def card(self, card_id: str) -> CardEntry:
        for card in self.cards:
            if card.card_id == card_id:
                return card
        raise KeyError(f"卡片字典里没有 card_id={card_id!r}")

    def validate(self, aruco: ArucoDictionary) -> None:
        if aruco.name != self.aruco_dictionary:
            raise ValueError(
                f"卡片字典声明使用 {self.aruco_dictionary}，但传入的是 {aruco.name}"
            )
        ids: dict[str, CardEntry] = {}
        markers: dict[int, CardEntry] = {}
        for card in self.cards:
            if card.card_id in ids:
                raise ValueError(f"card_id 重复：{card.card_id}")
            if card.marker_id in markers:
                raise ValueError(
                    f"marker_id {card.marker_id} 被 {markers[card.marker_id].card_id} 与 "
                    f"{card.card_id} 重复登记"
                )
            if not 0 <= card.marker_id <= aruco.max_marker_id:
                raise ValueError(
                    f"{card.card_id}: marker_id {card.marker_id} 超出 {aruco.name} 的范围 "
                    f"0..{aruco.max_marker_id}"
                )
            if not card.card_face:
                raise ValueError(f"{card.card_id}: card_face 不能为空")
            ids[card.card_id] = card
            markers[card.marker_id] = card


def load_aruco_dictionaries() -> dict[str, ArucoDictionary]:
    raw = read_json("aruco_dictionaries.json")
    dictionaries: dict[str, ArucoDictionary] = {}
    for spec in raw["dictionaries"]:
        aruco = ArucoDictionary(
            name=str(spec["name"]),
            marker_modules=int(spec["marker_modules"]),
            border_bits=int(spec["border_bits"]),
            codes=tuple(str(code) for code in spec["codes"]),
            source=str(spec.get("source", "")),
        )
        aruco.validate()
        dictionaries[aruco.name] = aruco
    if not dictionaries:
        raise ValueError("aruco_dictionaries.json 里没有任何字典")
    return dictionaries


def load_card_dictionary(
    aruco_dictionaries: dict[str, ArucoDictionary] | None = None,
) -> CardDictionary:
    dictionaries = aruco_dictionaries or load_aruco_dictionaries()
    raw = read_json("card_dictionary.json")
    cards = tuple(
        CardEntry(
            card_id=str(spec["card_id"]),
            marker_id=int(spec["marker_id"]),
            card_type=str(spec["card_type"]),
            card_face=str(spec["card_face"]),
            label=str(spec.get("label", "")),
        )
        for spec in raw["cards"]
    )
    card_dictionary = CardDictionary(
        version=str(raw["dictionary_version"]),
        aruco_dictionary=str(raw["aruco_dictionary"]),
        cards=cards,
    )
    aruco = dictionaries.get(card_dictionary.aruco_dictionary)
    if aruco is None:
        raise ValueError(
            f"卡片字典引用了未定义的 ArUco 字典 {card_dictionary.aruco_dictionary}"
        )
    card_dictionary.validate(aruco)
    return card_dictionary
