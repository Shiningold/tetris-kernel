"""tools/ 下的共享小工具。

五个脚本各自独立可跑（`python tools/xxx.py`），彼此之间不互相 import；
只有这一份低层工具是共享的：路径、稳定哈希、换行归一化、输出。

**共享的只有工具函数，不共享任何名单**——哪些名字合法、哪些取值域合法，
一律从 `contracts/` 的声明推导（架构 §4.4 末条）。这里不存任何"名单"：
多存一份就是多一份会悄悄过期的副本，声明改了它没跟着改，基线却显示全绿。
"""

from __future__ import annotations

import hashlib
import io
import sys
from collections.abc import Iterator
from pathlib import Path

#: 仓库根目录（`tools/` 的上一级）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 源码根目录（包在这里）。
SRC_ROOT = REPO_ROOT / "src"

#: 契约包目录（公共叶子）。
CONTRACTS_DIR = SRC_ROOT / "tetris_kernel" / "contracts"

#: 冻结区（内核 + 规则）。
FROZEN_DIRS = (SRC_ROOT / "tetris_kernel" / "kernel", SRC_ROOT / "tetris_kernel" / "rules")

#: 基线目录。
BASELINE_DIR = REPO_ROOT / "tests" / "baseline"

#: 契约基线文件（只增不改）。
CONTRACT_BASELINE = BASELINE_DIR / "contracts_baseline.json"

#: 冻结区内容哈希基线文件。
FROZEN_BASELINE = BASELINE_DIR / "kernel_hashes.json"

#: 每帧事件数基线文件（S16 采集 · 行为基线：内核每帧多发一条事件它就红）。
FRAME_EVENTS_BASELINE = BASELINE_DIR / "frame_events.json"

#: 契约 v1 冻结清单。
CONTRACT_CHECKLIST = REPO_ROOT / "docs" / "契约v1清单.md"

#: 哈希摘要长度（字节）。16 字节 = 32 个十六进制字符，够用且短。
HASH_SIZE = 16

#: 哈希算法名（写进基线文件的自述块，换算法时要看得见）。
HASH_ALGORITHM = f"blake2b-{HASH_SIZE}"


def configure_output() -> None:
    """让输出在非 UTF-8 控制台下也不炸。

    Windows 控制台默认 GBK，脚本里的中文提示里若混进 GBK 编不出的字符，
    `print` 会抛 `UnicodeEncodeError`——**报错信息本身报错**，比没有提示还糟。
    这里只放宽编码错误的处理，不改编码本身（改了会在别的机器上花屏）。
    """
    for stream in (sys.stdout, sys.stderr):
        # `sys.stdout` 的类型是 `TextIO`，而 `reconfigure` 只在 `TextIOWrapper`
        # 上——**用 isinstance 收窄**，而不是 `# type: ignore` 把这一行变成
        # "永不检查的地方"。
        if not isinstance(stream, io.TextIOWrapper):
            continue
        try:
            stream.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass


def blake2b_hex(text: str, *, size: int = HASH_SIZE) -> str:
    """稳定哈希（blake2b）。

    **不用内置 `hash()`**——它受 `PYTHONHASHSEED` 影响，跨进程必变，
    而基线要的就是跨进程稳定（S7 的随机源状态指纹也是同一条理由，ADR-060）。
    """
    return hashlib.blake2b(text.encode("utf-8"), digest_size=size).hexdigest()


def normalize_newlines(text: str) -> str:
    """把 CRLF / CR 归一化成 LF。

    **只动换行符**：注释、缩进、空格一律不动——冻结区基线靠它跨平台可比，
    而"改个注释就该被抓到"正是它要的效果（plan-M0.md S6 验证方案第 3 条）。

    **它必须真的承重**：调用方要**按原样读文件**（`newline=""`，不做通用
    换行翻译），归一化由这里做。`Path.read_text()` 默认走通用换行，CRLF /
    裸 CR 已经被它吃掉了——那样这层归一化就是死代码，哪天有人改回
    `newline=""` 之外的读法，跨平台可比性会**静默**失效，而注释还写着
    "这里归一化了"（批次 D 审查 D10）。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def iter_files(root: Path) -> Iterator[Path]:
    """遍历目录下的文件（跳过缓存目录），按路径排序 → 输出顺序稳定。"""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        yield path


def iter_python_files(root: Path) -> Iterator[Path]:
    """遍历目录下的 `.py` 文件（跳过缓存目录），按路径排序。"""
    for path in iter_files(root):
        if path.suffix == ".py":
            yield path


def relative_to_repo(path: Path) -> str:
    """相对仓库根的 POSIX 路径（报告与基线里的键都用这个写法，跨平台一致）。"""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()
