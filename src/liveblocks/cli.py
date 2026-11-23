"""命令行入口。

- 骨架阶段（LB-001）：``--version``；
- LB-002：``synth`` 子命令组（``list`` / ``generate`` / ``verify``）；
- LB-003：``recognize`` 子命令组：

  - ``liveblocks recognize detect --image <png>``   输出符合 `docs/PROTOCOL.md` §3 的 JSON
  - ``liveblocks recognize report``                 对 fixtures 目录跑识别并打印准确率/漏检统计

``--version`` 与 ``python -m liveblocks --version`` 的行为保持不变。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__

PROG = "liveblocks"

#: fixtures 的默认随机种子（改它会让入库 fixtures 全部失效，需同时重新生成）。
DEFAULT_SEED = 20261123
DEFAULT_SCENE_DIR = Path("fixtures/scenes")


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="序界（LiveBlocks）命令行入口：版本信息 + 合成卡片图像生成器（LB-002）。",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本号后退出",
    )

    subparsers = parser.add_subparsers(dest="command")

    synth = subparsers.add_parser(
        "synth",
        help="合成卡片图像生成器（LB-002）：生成虚拟卡片图像与真值 JSON",
        description="按卡片字典与版式定义生成 ArUco 卡片图像；不做识别。",
    )
    synth_sub = synth.add_subparsers(dest="synth_command", required=True)

    generate = synth_sub.add_parser("generate", help="生成图像 + 真值 JSON")
    generate.add_argument(
        "--scene",
        default="all",
        help="场景预设名，逗号分隔；all 表示全部预设（默认 all）",
    )
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"随机种子（默认 {DEFAULT_SEED}）")
    generate.add_argument("--out", type=Path, default=DEFAULT_SCENE_DIR, help="输出目录（默认 fixtures/scenes）")
    generate.set_defaults(handler=_cmd_synth_generate)

    listing = synth_sub.add_parser("list", help="列出场景预设、卡面版式与卡片字典")
    listing.set_defaults(handler=_cmd_synth_list)

    verify = synth_sub.add_parser("verify", help="重生成并与目录内已入库的图像/真值比对")
    verify.add_argument("--dir", type=Path, default=DEFAULT_SCENE_DIR, help="fixtures 目录（默认 fixtures/scenes）")
    verify.add_argument(
        "--seed",
        type=int,
        default=None,
        help="强制使用该种子重生成（默认用真值里记录的种子）",
    )
    verify.set_defaults(handler=_cmd_synth_verify)

    recognize = subparsers.add_parser(
        "recognize",
        help="识别模块（LB-003）：ArUco 标记候选 → 卡片候选",
        description="对一张灰度图做标记检测与卡片字典映射，输出协议 §3 的候选列表 JSON。",
    )
    recognize_sub = recognize.add_subparsers(dest="recognize_command", required=True)

    detect = recognize_sub.add_parser("detect", help="识别单张图片，输出候选列表 JSON")
    detect.add_argument("--image", type=Path, required=True, help="输入 PNG（8bit 灰度）")
    detect.add_argument("--frame-id", default=None, help="帧标识（默认由文件名推导 f-<主干>）")
    detect.add_argument(
        "--card-layout",
        default=None,
        help="采集用的卡面版式（默认取 synth_layouts.json 的 default_card_layout）",
    )
    detect.add_argument(
        "--card-dictionary",
        type=Path,
        default=None,
        help="替换卡片字典文件（格式同 data/card_dictionary.json，用于验证 unknown_card）",
    )
    detect.add_argument(
        "--emit",
        choices=("cards", "markers"),
        default="cards",
        help="输出哪个对象：cards（§3.2，默认）或 markers（§3.1）",
    )
    detect.add_argument(
        "--intrinsics",
        default=None,
        help="相机内参 fx,fy,cx,cy；给了才会在 markers 里输出 pose",
    )
    detect.add_argument("--source", default="synthetic", help="帧来源（synthetic|camera|replay）")
    detect.add_argument("--out", type=Path, default=None, help="写到文件；默认打印到标准输出")
    detect.set_defaults(handler=_cmd_recognize_detect)

    report = recognize_sub.add_parser(
        "report", help="对 fixtures 目录跑识别并输出准确率 / 漏检统计"
    )
    report.add_argument("--dir", type=Path, default=DEFAULT_SCENE_DIR, help="场景目录（默认 fixtures/scenes）")
    report.add_argument(
        "--card-layout",
        default=None,
        help="强制使用该卡面版式（默认按每个场景真值里的 card_layout，即采集配置）",
    )
    report.add_argument("--json", type=Path, default=None, help="把统计另存为 JSON")
    report.add_argument(
        "--no-intrinsics",
        action="store_true",
        help="不把真值里的相机内参当作采集配置传入（则输出不含 pose）",
    )
    report.set_defaults(handler=_cmd_recognize_report)

    return parser


def _resolve_scenes(requested: str) -> list[str]:
    from .synth import SynthContext

    ctx = SynthContext.load()
    available = sorted(ctx.presets)
    if requested.strip() == "all":
        return available
    names = [part.strip() for part in requested.split(",") if part.strip()]
    if not names:
        raise SystemExit("--scene 不能为空；用 all 或逗号分隔的预设名")
    unknown = [name for name in names if name not in ctx.presets]
    if unknown:
        raise SystemExit(
            f"未知场景预设：{', '.join(unknown)}；可用：{', '.join(available)}"
        )
    return names


def _cmd_synth_generate(args: argparse.Namespace) -> int:
    from .synth import SynthContext, generate_scene, write_index, write_scene

    ctx = SynthContext.load()
    names = _resolve_scenes(args.scene)
    scenes = [generate_scene(name, seed=args.seed, context=ctx) for name in names]
    for scene in scenes:
        image_path, truth_path = write_scene(args.out, scene)
        print(
            f"生成 {scene.name}: {image_path.name} ({scene.width}x{scene.height}, "
            f"{len(scene.image_png)} 字节, {len(scene.ground_truth['cards'])} 张卡) + {truth_path.name}"
        )
    if set(names) == set(ctx.presets):
        index_path = write_index(args.out, scenes, seed=args.seed)
        print(f"写入索引: {index_path}")
    print(f"种子 {args.seed}，输出目录 {args.out}")
    return 0


def _cmd_synth_list(args: argparse.Namespace) -> int:
    del args
    from .synth import SynthContext

    ctx = SynthContext.load()
    print(f"卡片字典 {ctx.card_dictionary.version}（{ctx.card_dictionary.aruco_dictionary}）：")
    for card in ctx.card_dictionary.cards:
        print(f"  {card.card_id:<4} marker_id={card.marker_id:<3} {card.card_type:<9} {card.card_face}")
    print("")
    print("卡面版式：")
    for name, layout in sorted(ctx.layouts.items()):
        print(
            f"  {name:<12} {layout.image_width_mm:g}x{layout.image_height_mm:g} mm，"
            f"标记 {layout.marker_size_mm:g} mm，{layout.pixels_per_mm} px/mm"
        )
    print("")
    print("场景预设：")
    for name in sorted(ctx.presets):
        spec = ctx.presets[name]
        print(f"  {name:<16} {spec.width}x{spec.height}，{len(spec.placements)} 张卡 — {spec.description}")
    return 0


def _cmd_synth_verify(args: argparse.Namespace) -> int:
    from .synth import SynthContext
    from .synth.generator import verify_directory

    ctx = SynthContext.load()
    seeds = None
    if args.seed is not None:
        seeds = {name: args.seed for name in ctx.presets}
    rows = verify_directory(args.dir, seeds=seeds, context=ctx)
    if not rows:
        print("没有可验证的场景")
        return 1
    failures = 0
    for row in rows:
        flag = "OK  " if row.ok else "FAIL"
        if not row.ok:
            failures += 1
        detail = f"字节={'同' if row.image_ok else '异'} 像素={'同' if row.pixel_ok else '异'} 真值={'同' if row.ground_truth_ok else '异'}"
        suffix = f" — {row.note}" if row.note else ""
        print(f"{flag} {row.name:<16} seed={row.seed:<10} {detail}{suffix}")
    print(f"\n{len(rows) - failures}/{len(rows)} 个场景与入库 fixtures 完全一致")
    return 0 if failures == 0 else 1


def _parse_intrinsics(text: str):
    from .vision import Intrinsics

    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) != 4:
        raise SystemExit("--intrinsics 需要 4 个数：fx,fy,cx,cy")
    try:
        fx, fy, cx, cy = (float(part) for part in parts)
    except ValueError as error:
        raise SystemExit(f"--intrinsics 解析失败：{error}") from error
    return Intrinsics(fx=fx, fy=fy, cx=cx, cy=cy)


def _build_registry(card_layout: str | None, card_dictionary: Path | None):
    from .vision import CardRegistry
    from .vision.cards import load_card_dictionary_file

    dictionary = None
    if card_dictionary is not None:
        dictionary = load_card_dictionary_file(card_dictionary)
    return CardRegistry.load(card_dictionary=dictionary, capture_layout=card_layout)


def _cmd_recognize_detect(args: argparse.Namespace) -> int:
    import json

    from .vision import recognize_path

    if not args.image.is_file():
        raise SystemExit(f"找不到图像：{args.image}")
    registry = _build_registry(args.card_layout, args.card_dictionary)
    intrinsics = _parse_intrinsics(args.intrinsics) if args.intrinsics else None
    result = recognize_path(
        args.image,
        registry=registry,
        frame_id=args.frame_id,
        source=args.source,
        intrinsics=intrinsics,
    )
    document = (result.cards if args.emit == "cards" else result.markers).to_dict()
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.out is None:
        print(text, end="")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    kind = document["kind"]
    count = len(document["cards"]) if args.emit == "cards" else len(document["markers"])
    print(f"写入 {args.out}（{kind}，{count} 项，frame_id={document['frame_id']}）")
    return 0


def _cmd_recognize_report(args: argparse.Namespace) -> int:
    import json

    from .vision import evaluate_directory, format_report

    registry = _build_registry(args.card_layout, None)
    report = evaluate_directory(
        args.dir,
        registry=registry,
        layout_override=args.card_layout,
        intrinsics_from_truth=not args.no_intrinsics,
    )
    if not report.scenes:
        print(f"{args.dir} 里没有可评估的合成场景真值")
        return 1
    text = format_report(report)
    print(text, end="")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"统计已写入 {args.json}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """命令行主函数，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"{PROG} {__version__}")
        return 0

    handler = getattr(args, "handler", None)
    if handler is not None:
        return int(handler(args))

    parser.print_help(sys.stdout)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
