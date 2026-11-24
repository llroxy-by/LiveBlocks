"""心跳帧与 LB-033 模拟器**逐字节一致**（用模拟器做的协议比对测试）。

做法：让 pty 上的设备模拟器（`liveblocks.hw.device.DeviceSimulator`，LB-033 的产物）真的发出
`HEARTBEAT` 帧，把每一帧的 `seq` / `uptime_ms` / `steps` 原样喂给**固件编码器**
（主机 gcc 编出来的 `firmware/main/lb_frame.c` + `lb_json.c` + `lb_heartbeat.c`），
再比对两边的字节。

比的是**整帧**（帧头、长度、序号、JSON 载荷、CRC），而不是「字段看起来一样」：

- 帧头 / 类型 / CRC 错了 → 字节不等；
- 载荷键序错了（`uptime_ms` 跑到 `steps` 前面）→ 字节不等；
- 浮点毫秒格式错了（`12.500` vs `12.5`、`60000` vs `60000.0`）→ 字节不等。

证据口径：模拟器证据，不是硬件证据。真机上的心跳周期与抖动属「待人工验证」
（`docs/HW.md` §8 HW-5、`docs/FIRMWARE.md` §7）。
"""

from __future__ import annotations

import time

import pytest

from liveblocks.firmware import HostTool, format_uptime_ms
from liveblocks.hw import DeviceSimulator, FrameParser, PtyTransport, command_frame
from liveblocks.hw.frames import KIND_EVENT, Frame
from liveblocks.hw.protocol import EVENTS_BY_NAME

HEARTBEAT_CODE = EVENTS_BY_NAME["HEARTBEAT"].code

#: 让模拟器每 20 ms 主动上行一帧心跳（默认是关的，见 DeviceSimulator.heartbeat_ms）。
HEARTBEAT_MS = 20.0

#: 采集几帧来比。
HEARTBEAT_SAMPLES = 4


def collect_simulator_heartbeats(count: int = HEARTBEAT_SAMPLES, timeout: float = 10.0) -> list[Frame]:
    """从**真跑着的**模拟器上收 `HEARTBEAT` 帧。"""
    simulator = DeviceSimulator(heartbeat_ms=HEARTBEAT_MS)
    simulator.plug()
    transport = PtyTransport(simulator.slave_path)
    transport.open()
    parser = FrameParser()
    frames: list[Frame] = []
    try:
        # 先握手（模拟器按协议要求从 HELLO 开始一条链路）
        transport.write(command_frame("HELLO", 1, {"client": "lb034-parity"}).encode())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(frames) < count:
            chunk = transport.read(0.05)
            if chunk is None:
                continue
            for frame in parser.feed(chunk):
                if frame.kind == KIND_EVENT and frame.type == HEARTBEAT_CODE:
                    frames.append(frame)
        assert not parser.drain_errors(), "模拟器这一侧不该产生坏帧"
    finally:
        transport.close()
        simulator.close()
    assert len(frames) >= count, f"模拟器只发出 {len(frames)} 帧心跳（< {count}），采集失败"
    return frames


@pytest.fixture(scope="module")
def simulator_heartbeats() -> list[Frame]:
    return collect_simulator_heartbeats()


def test_simulator_heartbeats_look_like_the_documented_frame(simulator_heartbeats: list[Frame]) -> None:
    for frame in simulator_heartbeats:
        assert frame.encode()[:2] == b"LB"
        assert frame.kind == KIND_EVENT
        assert frame.type == HEARTBEAT_CODE
        body = frame.body()
        assert set(body) == {"steps", "uptime_ms"}
        assert isinstance(body["uptime_ms"], float)
        # 键排序是「C 与 Python 逐字节一致」的前提（docs/HW.md §2.2）
        assert frame.payload.startswith(b'{"steps":')


def test_firmware_heartbeat_bytes_equal_simulator_bytes(
    firmware_host_tool: HostTool, simulator_heartbeats: list[Frame]
) -> None:
    """核心验收：整帧逐字节相同。"""
    for frame in simulator_heartbeats:
        body = frame.body()
        uptime_text = format_uptime_ms(body["uptime_ms"])
        mine = firmware_host_tool.heartbeat_bytes(frame.seq, uptime_text, body["steps"])
        assert mine == frame.encode(), (
            f"seq={frame.seq} uptime_ms={uptime_text} steps={body['steps']} 的帧不一致：\n"
            f"固件：{mine.hex(' ')}\n模拟器：{frame.encode().hex(' ')}"
        )


def test_firmware_payload_text_equal_simulator_payload(
    firmware_host_tool: HostTool, simulator_heartbeats: list[Frame]
) -> None:
    """单独把载荷比一遍：键序 / 浮点格式 / 分隔符错了这里会立刻暴露。"""
    for frame in simulator_heartbeats:
        body = frame.body()
        mine = firmware_host_tool.heartbeat_bytes(frame.seq, format_uptime_ms(body["uptime_ms"]), body["steps"])
        payload_start = 9
        payload_end = payload_start + frame.payload_len if hasattr(frame, "payload_len") else None
        if payload_end is None:  # Frame 里载荷长度由 LEN 字段决定
            payload_end = payload_start + int.from_bytes(mine[7:9], "little")
        assert mine[payload_start:payload_end] == frame.payload


def test_uptime_text_on_the_wire_matches_python_repr(simulator_heartbeats: list[Frame]) -> None:
    """线上那个毫秒文本，必须就是 `format_uptime_ms` 的形状（固件侧照它重建）。"""
    for frame in simulator_heartbeats:
        body = frame.body()
        text = format_uptime_ms(body["uptime_ms"])
        assert f'"uptime_ms":{text}'.encode() in frame.payload


def test_comparison_is_not_vacuous(firmware_host_tool: HostTool) -> None:
    """反向验证：换一个字节 / 换一个序号，比对必须失败（否则上面几条只是永真式）。"""
    frame = collect_simulator_heartbeats(count=1)[0]
    body = frame.body()
    uptime_text = format_uptime_ms(body["uptime_ms"])

    same = firmware_host_tool.heartbeat_bytes(frame.seq, uptime_text, body["steps"])
    assert same == frame.encode()

    other_seq = firmware_host_tool.heartbeat_bytes(frame.seq + 1, uptime_text, body["steps"])
    assert other_seq != frame.encode()

    other_uptime = firmware_host_tool.heartbeat_bytes(frame.seq, format_uptime_ms(body["uptime_ms"] + 1.0), body["steps"])
    assert other_uptime != frame.encode()

    other_steps = firmware_host_tool.heartbeat_bytes(frame.seq, uptime_text, body["steps"] + 1)
    assert other_steps != frame.encode()
