"""回放系统：录像录制与回放，以及**存档格式**。

**只订阅事件 + 派发命令**，不自己开文件（落盘经宿主注入的 sink · ADR-064），
也**不造内核**（起内核是宿主的事 · 架构 §4.1）。

**存档侧**（`archive.py` · M2-S10）是**纯数据**：它管"存档这份数据长什么样"，
**不 import `kernel.core`**——内核结构 ↔ 存档数据的映射在宿主层
（`host/archive.py`）。**存档 ≠ 录像**：两种格式，不共用一套。
"""

from .archive import (
    ARCHIVE_FILENAME_PREFIX,
    ARCHIVE_SUFFIX,
    ARCHIVE_VERSION,
    Archive,
    ArchivedActive,
    ArchivedBoard,
    ArchivedState,
    archive_filename,
    archive_from_json,
    archive_to_json,
)
from .format import (
    LEGACY_RECORDING_VERSION,
    RECORDING_FILENAME_PREFIX,
    RECORDING_SUFFIX,
    RECORDING_VERSION,
    SUPPORTED_RECORDING_VERSIONS,
    FiveItems,
    FourItems,
    RecordedCommand,
    Recording,
    RecordingOutcome,
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
    "ARCHIVE_FILENAME_PREFIX",
    "ARCHIVE_SUFFIX",
    "ARCHIVE_VERSION",
    "CHECKPOINT_INTERVAL_FRAMES",
    "CLOCK_TICK_EVENT",
    "FRAME_STEPPED_EVENT",
    "GAME_OVER_EVENT",
    "LEGACY_RECORDING_VERSION",
    "PLAYBACK_STARTED_EVENT",
    "PLAYBACK_STOPPED_EVENT",
    "RECORDING_FILENAME_PREFIX",
    "RECORDING_STARTED_EVENT",
    "RECORDING_STOPPED_EVENT",
    "RECORDING_SUFFIX",
    "RECORDING_VERSION",
    "SUPPORTED_RECORDING_VERSIONS",
    "TICK_COMMAND",
    "Archive",
    "ArchivedActive",
    "ArchivedBoard",
    "ArchivedState",
    "FiveItems",
    "FourItems",
    "OutcomeProvider",
    "PlaybackDriver",
    "PlaybackSession",
    "RecordedCommand",
    "Recording",
    "RecordingOutcome",
    "RecordingSystem",
    "Sink",
    "archive_filename",
    "archive_from_json",
    "archive_to_json",
    "recording_filename",
    "recording_from_jsonl",
    "recording_to_jsonl",
]
