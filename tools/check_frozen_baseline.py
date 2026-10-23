"""冻结区内容哈希基线：`kernel/` + `rules/`（架构 §4.4 / §7.6 · G4-1 的裁判）。

## 守什么

**冻结区 = `kernel/` + `rules/`**——变体落地（M4）时这两个目录的文件差异
必须为空。哈希基线就是那道裁判：内容一变就红。

- 哈希**整个文件内容**（不是逐行、不是符号表）：注释改了也该红。
- 哈希前**只归一化换行符**（CRLF / CR → LF）：Windows 上 checkout 出来是
  CRLF、Linux 上是 LF，不归一化的话同一份内容在两个平台上哈希不同，
  基线就成了"我在哪个平台提交的"探测器。**归一化的只有换行符**，别的
  一律不动（架构 §4.4）。
- `bus/` **不冻结**（M2 要给总线加中间件与历史查询，批批刷新基线会变成
  橡皮图章）——架构 §4.4 明说。

## 什么时候生成

**M0 收尾（S17）**，不是现在。开发期反复失效的基线是橡皮图章
（plan-M0.md S6 常见坑）。基线文件不存在时本脚本只提示、不报错。

## 用法

```
python tools/check_frozen_baseline.py                # 校验（默认）
python tools/check_frozen_baseline.py --update-baseline   # 生成 / 刷新
```

退出码：0 = 一致（或基线尚未生成）；1 = 冻结区有差异；2 = 用法或 IO 错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from _toolkit import (
    FROZEN_BASELINE,
    FROZEN_DIRS,
    HASH_ALGORITHM,
    SRC_ROOT,
    blake2b_hex,
    configure_output,
    iter_files,
    normalize_newlines,
    relative_to_repo,
)

#: 冻结区键的基准目录（键写成 `kernel/xxx.py` 这样的相对路径）。
KEY_ROOT = SRC_ROOT / "tetris_kernel"


@dataclass(frozen=True)
class FileHash:
    """一个文件的哈希。"""

    key: str
    digest: str


def collect_hashes() -> list[FileHash]:
    """冻结区全部文件的内容哈希（换行符已归一）。"""
    hashes: list[FileHash] = []
    for root in FROZEN_DIRS:
        for path in iter_files(root):
            key = path.resolve().relative_to(KEY_ROOT).as_posix()
            content = normalize_newlines(path.read_text(encoding="utf-8"))
            hashes.append(FileHash(key=key, digest=blake2b_hex(content)))
    return sorted(hashes, key=lambda item: item.key)


def baseline_document(hashes: list[FileHash]) -> dict[str, object]:
    """基线文件的内容（**不写时间戳**：逐字节可复现，git 里不产生噪声）。"""
    return {
        "meta": {
            "hashAlgorithm": HASH_ALGORITHM,
            "generatedBy": "tools/check_frozen_baseline.py",
            "scope": "kernel/ + rules/",
        },
        "files": {item.key: item.digest for item in hashes},
    }


def load_baseline(path: Path) -> dict[str, str]:
    """读基线文件，返回「相对路径 → 哈希」。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    files = document.get("files")
    if not isinstance(files, dict):
        raise ValueError(f"基线文件结构不对（缺 files）：{relative_to_repo(path)}")
    return {str(key): str(value) for key, value in files.items()}


def write_baseline(path: Path, hashes: list[FileHash]) -> None:
    """写基线文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(baseline_document(hashes), ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    configure_output()
    parser = argparse.ArgumentParser(description="冻结区内容哈希基线：kernel/ + rules/")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=FROZEN_BASELINE,
        help=f"基线文件路径（默认 {relative_to_repo(FROZEN_BASELINE)}）",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="生成 / 刷新基线",
    )
    args = parser.parse_args(argv)

    try:
        hashes = collect_hashes()
    except OSError as error:
        print(f"读冻结区失败：{error}", file=sys.stderr)
        return 2
    current = {item.key: item.digest for item in hashes}

    if args.update_baseline:
        write_baseline(args.baseline, hashes)
        print(f"已写入基线：{relative_to_repo(args.baseline)}（{len(hashes)} 个文件）")
        return 0

    if not args.baseline.exists():
        print(
            f"冻结区基线尚未生成：{relative_to_repo(args.baseline)} 不存在"
            "（按计划在 M0 收尾 / S17 生成）"
        )
        return 0

    try:
        baseline = load_baseline(args.baseline)
    except (OSError, ValueError) as error:
        print(f"读基线失败：{error}", file=sys.stderr)
        return 2

    added = sorted(key for key in current if key not in baseline)
    changed = sorted(key for key in baseline if key in current and baseline[key] != current[key])
    removed = sorted(key for key in baseline if key not in current)

    for key in added:
        print(f"冻结区新增文件（红）：{key}")
    for key in changed:
        print(f"冻结区内容变化（红）：{key}")
    for key in removed:
        print(f"冻结区文件消失（红）：{key}")

    print(
        f"冻结区 {len(current)} 个文件：新增 {len(added)} · 变化 {len(changed)}"
        f" · 消失 {len(removed)}"
    )
    if added or changed or removed:
        print("冻结区基线校验失败：内核 / 规则的内容变了。", file=sys.stderr)
        return 1
    print("冻结区基线校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
