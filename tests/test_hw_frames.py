"""串口帧格式（`docs/HW.md` §2）的单测：编码 / 解码 / CRC / 截断 / 坏帧重同步 / 序号。

本单要求「协议层单测覆盖帧解析的截断、CRC 错、序号乱序三类异常」，这三条在这里各有专门的用例，
并且都用**真实字节**（不是打桩）：截断用「逐个字节喂」、CRC 错用「翻一位」、乱序用「跳号 / 重号」。
"""

from __future__ import annotations

import pytest

from liveblocks.hw import (
    CRC_SIZE,
    HEADER_SIZE,
    MAGIC,
    MAX_PAYLOAD,
    MAX_SEQ,
    WIRE_VERSION,
    Frame,
    FrameError,
    FrameParser,
    SequenceTracker,
    crc16_ccitt,
    decode_frame,
    encode_body,
    encode_frames,
    event_frame,
    next_seq,
)

HELLO_PONG = event_frame("PONG", 1, {"nonce": 7, "uptime_ms": 12.5})


def test_crc16_ccitt_matches_the_standard_check_value() -> None:
    """CRC-16/CCITT-FALSE 的标准校验值：``b"123456789"`` → ``0x29B1``。"""
    assert crc16_ccitt(b"123456789") == 0x29B1


def test_frame_header_layout_is_the_documented_one() -> None:
    raw = HELLO_PONG.encode()
    assert raw[:2] == MAGIC == b"LB"
    assert raw[2] == WIRE_VERSION
    assert raw[3] == 2  # KIND_EVENT
    assert raw[4] == 0x86  # EVT PONG
    assert int.from_bytes(raw[5:7], "little") == 1  # SEQ
    assert int.from_bytes(raw[7:9], "little") == len(HELLO_PONG.payload)  # LEN
    assert len(raw) == HEADER_SIZE + len(HELLO_PONG.payload) + CRC_SIZE
    # CRC 覆盖 VER..PAYLOAD（不含 MAGIC）
    body = raw[2 : HEADER_SIZE + len(HELLO_PONG.payload)]
    assert int.from_bytes(raw[-2:], "little") == crc16_ccitt(body)


def test_roundtrip_preserves_kind_type_seq_and_body() -> None:
    frame, consumed = decode_frame(HELLO_PONG.encode())
    assert consumed == len(HELLO_PONG.encode())
    assert (frame.kind, frame.type, frame.seq) == (2, 0x86, 1)
    assert frame.body() == {"nonce": 7, "uptime_ms": 12.5}


def test_payload_encoding_is_canonical() -> None:
    """键排序 + 紧凑分隔符：同一对象在不同语言 / 不同进程里编码出同样的字节。"""
    assert encode_body({"b": 1, "a": [1, 2]}) == b'{"a":[1,2],"b":1}'
    assert Frame.build(2, 0x86, 3, {"b": 1, "a": 2}).payload == Frame.build(2, 0x86, 3, {"a": 2, "b": 1}).payload


def test_decode_consumes_exactly_one_frame_when_two_are_concatenated() -> None:
    second = event_frame("HEARTBEAT", 2, {"uptime_ms": 1.0, "steps": 0})
    stream = encode_frames([HELLO_PONG, second])
    first, used = decode_frame(stream)
    assert first.seq == 1
    assert used == len(HELLO_PONG.encode())
    rest, used2 = decode_frame(stream[used:])
    assert rest.seq == 2 and rest.type == 0x87


# -- 异常一：截断 -------------------------------------------------------------
@pytest.mark.parametrize("cut", [1, 2, 5, HEADER_SIZE - 1, HEADER_SIZE, HEADER_SIZE + 3])
def test_truncated_frame_is_reported_as_truncated(cut: int) -> None:
    raw = HELLO_PONG.encode()
    with pytest.raises(FrameError) as info:
        decode_frame(raw[:cut])
    assert info.value.reason == "truncated"
    assert info.value.detail["have"] == cut


def test_parser_keeps_truncated_tail_and_completes_it_later() -> None:
    """逐字节喂：最后一字节之前不许出帧，最后一字节一到就得有一帧。"""
    raw = HELLO_PONG.encode()
    parser = FrameParser()
    frames = []
    for index, byte in enumerate(raw):
        gotten = parser.feed(bytes([byte]))
        frames.extend(gotten)
        if index < len(raw) - 1:
            assert gotten == [] and parser.pending == raw[: index + 1]
    assert len(frames) == 1 and frames[0].body()["nonce"] == 7
    assert parser.pending == b""
    assert parser.drain_errors() == []


def test_parser_splits_two_frames_from_one_chunk() -> None:
    second = event_frame("PONG", 2, {"nonce": 8, "uptime_ms": 1.0})
    parser = FrameParser()
    frames = parser.feed(encode_frames([HELLO_PONG, second]))
    assert [frame.seq for frame in frames] == [1, 2]


# -- 异常二：CRC 错 -----------------------------------------------------------
def test_crc_error_is_reported_and_frame_dropped() -> None:
    raw = bytearray(HELLO_PONG.encode())
    raw[HEADER_SIZE] ^= 0xFF  # 翻载荷第一个字节
    parser = FrameParser()
    frames = parser.feed(bytes(raw))
    assert frames == []
    errors = parser.drain_errors()
    assert [error.reason for error in errors] == ["crc"]
    assert "CRC 不符" in errors[0].message
    assert parser.pending == b""


def test_crc_error_does_not_swallow_the_next_frame() -> None:
    """坏帧之后紧跟好帧：坏帧只丢自己，好帧照收。"""
    broken = bytearray(HELLO_PONG.encode())
    broken[-1] ^= 0xFF  # 翻 CRC 自身
    good = event_frame("PONG", 2, {"nonce": 9, "uptime_ms": 2.0})
    parser = FrameParser()
    frames = parser.feed(bytes(broken) + good.encode())
    assert [frame.seq for frame in frames] == [2]
    assert [error.reason for error in parser.drain_errors()] == ["crc"]


def test_length_over_limit_is_rejected() -> None:
    header = bytearray(HELLO_PONG.encode()[:HEADER_SIZE])
    header[7:9] = (MAX_PAYLOAD + 1).to_bytes(2, "little")
    with pytest.raises(FrameError) as info:
        decode_frame(bytes(header) + b"\x00" * 8)
    assert info.value.reason == "length"


def test_unknown_version_kind_and_type_are_rejected() -> None:
    raw = bytearray(HELLO_PONG.encode())
    raw[2] = 99
    with pytest.raises(FrameError) as version:
        decode_frame(bytes(raw))
    assert version.value.reason == "bad_version"

    raw = bytearray(HELLO_PONG.encode())
    raw[3] = 7
    with pytest.raises(FrameError) as kind:
        decode_frame(bytes(raw))
    assert kind.value.reason == "bad_kind"

    raw = bytearray(HELLO_PONG.encode())
    raw[3] = 1  # 命令 kind 却带着事件 type
    with pytest.raises(FrameError) as kind_type:
        decode_frame(bytes(raw))
    assert kind_type.value.reason == "bad_type"


def test_bad_magic_resyncs_one_byte_at_a_time() -> None:
    """帧头错时只丢 1 字节继续找 ``LB``：垃圾前缀 + 好帧 → 好帧照收。"""
    garbage = b"\x00\xff\x13garbage"
    parser = FrameParser()
    frames = parser.feed(garbage + HELLO_PONG.encode())
    assert [frame.seq for frame in frames] == [1]
    assert parser.skipped_bytes == len(garbage)
    assert all(error.reason in ("bad_magic", "bad_version", "bad_kind") for error in parser.drain_errors())


def test_payload_must_be_a_json_object() -> None:
    frame = Frame(kind=2, type=0x86, seq=1, payload=b"not json")
    with pytest.raises(FrameError) as info:
        frame.body()
    assert info.value.reason == "payload"
    frame = Frame(kind=2, type=0x86, seq=1, payload=b"[1,2]")
    with pytest.raises(FrameError) as info2:
        frame.body()
    assert info2.value.reason == "payload"


def test_oversized_payload_cannot_be_encoded() -> None:
    with pytest.raises(ValueError):
        Frame(kind=2, type=0x86, seq=1, payload=b"x" * (MAX_PAYLOAD + 1)).encode()


# -- 异常三：序号乱序 ---------------------------------------------------------
def test_sequence_tracker_accepts_in_order_and_wraps_at_max() -> None:
    tracker = SequenceTracker()
    for seq in (1, 2, 3):
        assert tracker.check(seq) == seq
    assert tracker.expected == 4
    assert next_seq(MAX_SEQ) == 1
    assert next_seq(0) == 1


@pytest.mark.parametrize("seq", [1, 3, 5, 0, MAX_SEQ])
def test_sequence_gap_or_duplicate_is_reported(seq: int) -> None:
    tracker = SequenceTracker()
    tracker.check(1)
    with pytest.raises(FrameError) as info:
        tracker.check(seq)  # 期望 2
    assert info.value.reason == "sequence"
    assert info.value.detail["expected"] == 2
    assert info.value.detail["actual"] == seq


def test_sequence_error_carries_expected_and_seen_counts() -> None:
    tracker = SequenceTracker()
    tracker.check(1)
    tracker.check(2)
    with pytest.raises(FrameError) as info:
        tracker.check(4)
    assert info.value.detail == {"expected": 3, "actual": 4, "seen": 2}


def test_sequence_tracker_reset_starts_again_from_one() -> None:
    """重连后新链路的序号从 1 重新开始（握手会重置两端的期望值）。"""
    tracker = SequenceTracker()
    tracker.check(1)
    tracker.reset()
    assert tracker.check(1) == 1


def test_frame_seq_out_of_range_cannot_be_encoded() -> None:
    with pytest.raises(ValueError):
        Frame(kind=2, type=0x86, seq=0, payload=b"{}").encode()
    with pytest.raises(ValueError):
        Frame(kind=2, type=0x86, seq=MAX_SEQ + 1, payload=b"{}").encode()
