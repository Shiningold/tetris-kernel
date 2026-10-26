"""回放系统：录像录制与回放。

**只订阅事件 + 派发命令**，不自己开文件（落盘经宿主注入的 sink · ADR-064）。
"""

from .format import (
    RECORDING_FILENAME_PREFIX,
    RECORDING_SUFFIX,
    RECORDING_VERSION,
    RecordedCommand,
    Recording,
    recording_filename,
    recording_from_jsonl,
    recording_to_jsonl,
)
from .recorder import (
    ACCEPTED_COMMAND_EVENT,
    CHECKPOINT_INTERVAL_FRAMES,
    CLOCK_TICK_EVENT,
    GAME_OVER_EVENT,
    RECORDING_STARTED_EVENT,
    RECORDING_STOPPED_EVENT,
    RecordingSystem,
    Sink,
)

__all__ = [
    "ACCEPTED_COMMAND_EVENT",
    "CHECKPOINT_INTERVAL_FRAMES",
    "CLOCK_TICK_EVENT",
    "GAME_OVER_EVENT",
    "RECORDING_FILENAME_PREFIX",
    "RECORDING_STARTED_EVENT",
    "RECORDING_STOPPED_EVENT",
    "RECORDING_SUFFIX",
    "RECORDING_VERSION",
    "RecordedCommand",
    "Recording",
    "RecordingSystem",
    "Sink",
    "recording_filename",
    "recording_from_jsonl",
    "recording_to_jsonl",
]
