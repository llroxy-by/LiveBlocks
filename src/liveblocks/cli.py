"""命令行入口（骨架版）。

骨架阶段只提供自检信息：``--version`` 打印版本号。
业务子命令（生成 / 识别 / 解析 / 执行 / 界面）在对应任务中加入。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__

PROG = "liveblocks"


def build_parser() -> argparse.ArgumentParser:
    """构造骨架阶段的命令行解析器。"""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="序界（LiveBlocks）命令行入口：骨架阶段只提供 --version。",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本号后退出",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """命令行主函数，返回进程退出码。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"{PROG} {__version__}")
        return 0

    parser.print_help(sys.stdout)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
