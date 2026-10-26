"""回放系统：录像录制与回放。

**只订阅事件 + 派发命令**，不自己开文件（落盘经宿主注入的 sink · ADR-064），
也**不造内核**（起内核是宿主的事 · 架构 §4.1）。
"""

from .format import (
    RECORDING_FILENAME_PREFIX,
    RECORDING_SUFFIX,
    RECORDING_VERSION,
    FourItems,
    RecordedCommand,
    Recording,
    recording_filename,
    recording_from_jsonl,
    recording_to_jsonl,
)
from .playback import (
    FRAME_STEPPED_EVENT,
    PLAYBACK_STARTED_EVENT,
    PLAYBACK_STOPPED_EVENT,
    TICK_COMMAND,
    PlaybackDriver,
    PlaybackSession,
)
from .recorder import (
    ACCEPTED_COMMAND_EVENT,
    CHECKPOINT_INTERVAL_FRAMES,
    CLOCK_TICK_EVENT,
    GAME_OVER_EVENT,
    RECORDING_STARTED_EVENT,
    RECORDING_STOPPED_EVENT,
    OutcomeProvider,
    RecordingSystem,
    Sink,
)

__all__ = [
    "ACCEPTED_COMMAND_EVENT",
    "CHECKPOINT_INTERVAL_FRAMES",
    "CLOCK_TICK_EVENT",
    "FRAME_STEPPED_EVENT",
    "GAME_OVER_EVENT",
    "PLAYBACK_STARTED_EVENT",
    "PLAYBACK_STOPPED_EVENT",
    "RECORDING_FILENAME_PREFIX",
    "RECORDING_STARTED_EVENT",
    "RECORDING_STOPPED_EVENT",
    "RECORDING_SUFFIX",
    "RECORDING_VERSION",
    "TICK_COMMAND",
    "FourItems",
    "OutcomeProvider",
    "PlaybackDriver",
    "PlaybackSession",
    "RecordedCommand",
    "Recording",
    "RecordingSystem",
    "Sink",
    "recording_filename",
    "recording_from_jsonl",
    "recording_to_jsonl",
]
