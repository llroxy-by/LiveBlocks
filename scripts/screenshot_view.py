#!/usr/bin/env python3
"""把调试视图的 HTML **真渲染**成 PNG（截图证据），并量化核对「不是空白图」。

Issue #20 要求「附截图作为证据」，而且截图必须是从真实界面渲染出来的，不能是 mock-up。
这个脚本因此只做两件事，中间不掺任何手绘内容：

1. 调 :func:`liveblocks.ui.render.build_view_from_file` 生成视图的 HTML（与命令行
   ``liveblocks ui view --html-out`` 完全同一份代码、同一份数据）；
2. 把这份 HTML 交给浏览器引擎（WebKitGTK）画出来，截整页存成 PNG。

渲染引擎走 GTK/WebKit，需要一个 X 显示：脚本发现没有 ``DISPLAY`` 时会自己用
``xvfb-run`` 重新执行（无头环境也能跑）。如果环境里连 WebKitGTK 都没有，脚本**直接失败**
并说明原因——绝不伪造截图。

用法：

.. code-block:: bash

   python scripts/screenshot_view.py --record fixtures/runs/sample-clipped.jsonl \
       --step 3 --out docs/evidence/lb-008-debug-view.png

   # 无头环境（脚本也会自己套 xvfb-run，这里显式写出来只是让它更清楚）
   xvfb-run -a python3 scripts/screenshot_view.py --record fixtures/runs/corrupt/missing-fields.jsonl \
       --out docs/evidence/lb-008-corrupt-record.png
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

#: 默认窗口宽度（视图是 3 列网格，1440 下一屏放得下五层）。
DEFAULT_WIDTH = 1440
#: 截图前给渲染引擎留下的收尾时间（毫秒）。
SETTLE_MS = 700


def _reexec_under_xvfb() -> None:
    """没有 X 显示时用 ``xvfb-run`` 重新执行自己（找不到就直接报错，不静默降级）。"""
    if os.environ.get("DISPLAY"):
        return
    runner = shutil.which("xvfb-run")
    if runner is None:
        raise SystemExit(
            "没有 DISPLAY 也找不到 xvfb-run：无法启动浏览器引擎渲染截图。\n"
            "这不是「没有界面」，而是本机缺少无头 X 服务；装 xvfb 后重跑，"
            "或在一台有图形界面的机器上执行本脚本。"
        )
    os.execvp(runner, [runner, "-a", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def _require_webkit():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import GLib, Gtk, WebKit2
    except (ImportError, ValueError) as exc:  # pragma: no cover - 环境相关
        raise SystemExit(
            "缺少 WebKitGTK（PyGObject + gir1.2-webkit2-4.1）：无法真渲染截图。\n"
            f"原始错误：{exc}\n"
            "本脚本**不会**退化成画一张假图；请装好渲染引擎后重跑。"
        ) from exc
    return GLib, Gtk, WebKit2


def render_html_to_png(html: str, out_path: Path, *, width: int = DEFAULT_WIDTH) -> tuple[int, int]:
    """用 WebKitGTK 把 HTML 渲染成整页 PNG，返回图像尺寸。"""
    GLib, Gtk, WebKit2 = _require_webkit()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lb-008-view-") as workdir:
        html_path = Path(workdir) / "view.html"
        html_path.write_text(html, encoding="utf-8")

        window = Gtk.OffscreenWindow()
        window.set_default_size(width, 900)
        webview = WebKit2.WebView()
        webview.set_size_request(width, 900)
        settings = webview.get_settings()
        settings.set_enable_javascript(False)  # 视图里本来就没有脚本，这里再确认一次
        settings.set_enable_webgl(False)
        window.add(webview)
        window.show_all()
        state: dict[str, object] = {"done": False, "error": ""}

        def on_snapshot(view, result, _data) -> None:
            try:
                surface = view.get_snapshot_finish(result)
            except Exception as exc:  # pragma: no cover - 环境相关
                state["error"] = f"{type(exc).__name__}: {exc}"
                Gtk.main_quit()
                return
            if surface is None:  # pragma: no cover - 环境相关
                state["error"] = "get_snapshot_finish 返回 None"
                Gtk.main_quit()
                return
            surface.write_to_png(str(out_path))
            state["size"] = (surface.get_width(), surface.get_height())
            state["done"] = True
            Gtk.main_quit()

        def take_snapshot() -> bool:
            webview.get_snapshot(
                WebKit2.SnapshotRegion.FULL_DOCUMENT, WebKit2.SnapshotOptions.NONE, None, on_snapshot, None
            )
            return False

        def on_load(view, event) -> None:
            if event == WebKit2.LoadEvent.FINISHED:
                GLib.timeout_add(SETTLE_MS, take_snapshot)

        webview.connect("load-changed", on_load)
        webview.load_uri(html_path.resolve().as_uri())
        GLib.timeout_add_seconds(60, lambda: (state.__setitem__("error", "渲染超时（60 s）"), Gtk.main_quit(), False)[2])
        Gtk.main()
    if not state.get("done"):
        raise SystemExit(f"渲染截图失败：{state.get('error') or '未知原因'}")
    return state["size"]  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 LB-008 调试视图真渲染成 PNG 截图")
    parser.add_argument("--record", type=Path, required=True, help="§8 运行记录（JSONL）")
    parser.add_argument("--step", type=int, default=None, help="单步位置（默认末帧）")
    parser.add_argument("--out", type=Path, required=True, help="输出 PNG 路径")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=f"窗口宽度（默认 {DEFAULT_WIDTH}）")
    parser.add_argument("--keep-html", type=Path, default=None, help="顺带把渲染用的 HTML 存一份")
    args = parser.parse_args(argv)

    _reexec_under_xvfb()

    from liveblocks.ui.evidence import screenshot_stats
    from liveblocks.ui.render import build_view_from_file

    if not args.record.is_file():
        raise SystemExit(f"找不到运行记录：{args.record}")
    view = build_view_from_file(args.record, step=args.step)
    html = view.render_html()
    if args.keep_html is not None:
        args.keep_html.parent.mkdir(parents=True, exist_ok=True)
        args.keep_html.write_text(html, encoding="utf-8")
        print(f"渲染用的 HTML 已写入 {args.keep_html}")
    width, height = render_html_to_png(html, args.out, width=args.width)
    stats = screenshot_stats(args.out.read_bytes())
    print(
        f"截图 {args.out}：{width}x{height}px，{args.out.stat().st_size} 字节，"
        f"{stats.describe()}，非空白={stats.non_blank}"
    )
    if not stats.non_blank:
        print("截图被判为空白图：渲染结果没有内容，拒绝把它当证据", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
