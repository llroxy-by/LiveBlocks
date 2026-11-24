"""标记页清单（``CardSheet`` JSON）：页面几何、卡片编号、期望朝向与产物指纹。

清单是「这一页应该长什么样」的机器可读声明，``sheet.verify`` 拿它跟重新识别的结果逐卡比对；
它同时是排版的唯一记录（哪张卡在第几页第几行第几列、裁切线在哪、静区多大），所以卡片的
批次编号（``batch_id`` + 页 + 位号）也写在这里，见 ``docs/CARD-SPEC.md`` §6。

清单里**没有时间戳**：同参数两次渲染必须给出同一份清单字节（``docs/CARD-SPEC.md`` §7 的
QC-05）。产物指纹（``pixel_sha256`` / ``png_sha256`` / ``pdf_sha256``）是像素的函数，因此
同样确定。
"""

from __future__ import annotations

import hashlib
from typing import Any

from .spec import HUMAN_VERIFICATION, SHEET_VERSION, CardSheetSpec

SCHEMA_VERSION = "0.0.1"
KIND = "CardSheet"


def build_manifest(
    spec: CardSheetSpec,
    *,
    width: int,
    height: int,
    pixels: bytes | bytearray,
    png: bytes,
    pdf: bytes,
) -> dict[str, Any]:
    """构造一份清单（键顺序固定，便于逐字节比对）。"""
    document = spec.to_dict()
    document = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "sheet_version": SHEET_VERSION,
        "generator": "liveblocks cardsheet（LB-032）",
        **document,
        "orientation": {
            "page_top_deg": 0,
            "per_card": "solid triangle at the card's bottom-left corner, apex pointing to the card top edge",
            "mark_mm": round(spec.orientation_mark_mm, 3),
        },
        "raster": {
            "kind": "png-grayscale8",
            "width_px": width,
            "height_px": height,
            "pixel_sha256": hashlib.sha256(bytes(pixels)).hexdigest(),
            "png_sha256": hashlib.sha256(png).hexdigest(),
            "png_bytes": len(png),
            "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
            "pdf_bytes": len(pdf),
        },
        "unmeasured": [item.to_dict() for item in _unmeasured()],
        "human_verification": [
            {"item": item, "steps": steps} for item, steps in HUMAN_VERIFICATION
        ],
    }
    return document


def _unmeasured():
    from .spec import unmeasured_items

    return unmeasured_items()


__all__ = ["KIND", "SCHEMA_VERSION", "build_manifest"]
