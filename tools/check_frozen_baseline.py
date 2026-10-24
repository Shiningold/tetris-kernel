"""冻结区内容哈希基线：`kernel/` + `rules/`（架构 §4.4 / §7.6 · G4-1 的裁判）。

## 守什么

**冻结区 = `kernel/` + `rules/`**——变体落地（M4）时这两个目录的文件差异
必须为空。哈希基线就是那道裁判：内容一变就红。

- 哈希**整个文件内容**（不是逐行、不是符号表）：注释改了也该红。
- 哈希前**只归一化换行符**（CRLF / CR → LF）：Windows 上 checkout 出来是
  CRLF、Linux 上是 LF，不归一化的话同一份内容在两个平台上哈希不同，
  基线就成了"我在哪个平台提交的"探测器。**归一化的只有换行符**，别的
  一律不动（架构 §4.4）。**归一化由 `normalize_newlines()` 做**，所以读文件
  必须**按原样读**（`newline=""`，不做通用换行翻译）——`Path.read_text()`
  默认会把 CRLF 吃成 LF，那样归一化就成了死代码，哪天有人改了读法，
  跨平台可比性会**静默**失效而注释还写着"这里归一化了"（批次 D 审查 D10）。
- `bus/` **不冻结**（M2 要给总线加中间件与历史查询，批批刷新基线会变成
  橡皮图章）——架构 §4.4 明说。

## 基线文件自己也要可信

冻结区基线天然免疫"基线被清空"（文件少了就是差异），但**口径要与契约
基线统一**：自述块里记着**它自己有多少个文件**（`meta.fileCount`），条数
对不上就是有人动过它 → 红（批次 D 审查 D1 的"口径统一"那一半）。

## 刷新要写明理由

`--update-baseline` **必须带 `--reason`**（不给就用法错误退出），并打印
**「旧基线 → 新基线」的差异摘要**：刷新不是无声覆盖（批次 D 审查 D4）。

## 什么时候生成

**M0 收尾（S17）**，不是现在。开发期反复失效的基线是橡皮图章
（plan-M0.md S6 常见坑）。基线文件不存在时本脚本只提示、不报错。

## 用法

```
python tools/check_frozen_baseline.py                # 校验（默认）
python tools/check_frozen_baseline.py --update-baseline --reason "<理由>"
```

退出码：0 = 一致（或基线尚未生成）；1 = 冻结区有差异 / 基线自身不可信；2 = 用法或 IO 错误。
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


@dataclass(frozen=True)
class BaselineDocument:
    """读进来的基线文件：文件哈希表 + 自述块。

    **自述块不能丢**：它记着"基线自己有多少个文件"——这是基线**自证没被动
    过**的依据（批次 D 审查 D1 的口径统一那一半）。
    """

    files: dict[str, str]
    meta: dict[str, object]


def _read_raw(path: Path) -> str:
    """按原样读文本：**不翻译换行符**（`newline=""`）。

    `Path.read_text()` 默认走通用换行，CRLF / 裸 CR 已经被它吃掉了——那样
    `normalize_newlines()` 就成了死代码，哪天有人改读法，跨平台可比性会
    **静默**失效而注释还写着"这里归一化了"（批次 D 审查 D10）。
    """
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def collect_hashes() -> list[FileHash]:
    """冻结区全部文件的内容哈希（换行符已归一）。"""
    hashes: list[FileHash] = []
    for root in FROZEN_DIRS:
        for path in iter_files(root):
            key = path.resolve().relative_to(KEY_ROOT).as_posix()
            content = normalize_newlines(_read_raw(path))
            hashes.append(FileHash(key=key, digest=blake2b_hex(content)))
    return sorted(hashes, key=lambda item: item.key)


def baseline_document(hashes: list[FileHash]) -> dict[str, object]:
    """基线文件的内容（**不写时间戳**：逐字节可复现，git 里不产生噪声）。

    **自述块里记着基线自己有多少个文件**（`fileCount`）：口径与契约基线
    统一——条数对不上就是有人动过它（批次 D 审查 D1）。
    """
    return {
        "meta": {
            "fileCount": len(hashes),
            "hashAlgorithm": HASH_ALGORITHM,
            "generatedBy": "tools/check_frozen_baseline.py",
            "scope": "kernel/ + rules/",
        },
        "files": {item.key: item.digest for item in hashes},
    }


def load_baseline(path: Path) -> BaselineDocument:
    """读基线文件，返回「文件哈希表 + 自述块」。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    files = document.get("files")
    if not isinstance(files, dict):
        raise ValueError(f"基线文件结构不对（缺 files）：{relative_to_repo(path)}")
    meta = document.get("meta")
    if not isinstance(meta, dict):
        raise ValueError(f"基线文件结构不对（缺 meta）：{relative_to_repo(path)}")
    return BaselineDocument(
        files={str(key): str(value) for key, value in files.items()},
        meta={str(key): value for key, value in meta.items()},
    )


def baseline_integrity_problems(baseline: BaselineDocument) -> list[str]:
    """基线文件自身不自洽的地方（**每一条都是红项**）。

    **口径与契约基线统一**：自述块记着基线自己有多少个文件，对不上就是
    有人动过它（批次 D 审查 D1）。冻结区天然免疫"被清空"（文件少了就是
    差异），但自证这一步不能少——两条基线的口径不一致本身就是线索。
    """
    problems: list[str] = []
    files = baseline.files

    if not files:
        problems.append("基线一个文件都没有：基线被清空了")

    declared = baseline.meta.get("fileCount")
    if not isinstance(declared, int):
        problems.append(f"基线自述块缺 fileCount（现在是 {declared!r}）：基线不是当前口径生成的")
    elif declared != len(files):
        problems.append(f"基线自述块记着 {declared} 个文件，实际只有 {len(files)} 个：基线被改过")

    return problems


def write_baseline(path: Path, hashes: list[FileHash]) -> None:
    """写基线文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(baseline_document(hashes), ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def _print_update_diff(path: Path, hashes: list[FileHash], reason: str | None) -> None:
    """打印「旧基线 → 新基线」的差异摘要。

    **刷新必须看得见改了什么**：理由与差异一起打印，刷新就不是无声覆盖
    （批次 D 审查 D4）。
    """
    print(f"刷新理由：{reason}")
    if not path.exists():
        print("旧基线不存在：本次是首次生成。")
        return
    try:
        old = load_baseline(path)
    except (OSError, ValueError) as error:
        # 旧基线读不出来不拦刷新——拦了就是"坏基线永远刷不掉"。
        print(f"旧基线读不出来（只报不拦）：{error}")
        return
    new = {item.key: item.digest for item in hashes}
    added = sorted(key for key in new if key not in old.files)
    changed = sorted(key for key in old.files if key in new and old.files[key] != new[key])
    removed = sorted(key for key in old.files if key not in new)
    print(f"旧基线 {len(old.files)} 个文件 → 新基线 {len(new)} 个文件")
    for key in added:
        print(f"  新增：{key}")
    for key in changed:
        print(f"  变化：{key}（{old.files[key]} → {new[key]}）")
    for key in removed:
        print(f"  消失：{key}")
    if not (added or changed or removed):
        print("  文件哈希无变化（只有自述块可能变了）")


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
        help="生成 / 刷新基线（必须带 --reason）",
    )
    parser.add_argument(
        "--reason",
        type=str,
        default=None,
        help="刷新基线的理由（--update-baseline 必须带）",
    )
    args = parser.parse_args(argv)

    if args.update_baseline and not args.reason:
        parser.error(
            '--update-baseline 必须带 --reason "<理由>"：基线刷新要写明理由，不做无声覆盖。'
        )

    try:
        hashes = collect_hashes()
    except OSError as error:
        print(f"读冻结区失败：{error}", file=sys.stderr)
        return 2
    current = {item.key: item.digest for item in hashes}

    if args.update_baseline:
        _print_update_diff(args.baseline, hashes, args.reason)
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

    problems = baseline_integrity_problems(baseline)
    if problems:
        for problem in problems:
            print(f"基线自身不可信（红）：{problem}")
        print("冻结区基线校验失败：基线文件被清空 / 残缺。", file=sys.stderr)
        return 1

    added = sorted(key for key in current if key not in baseline.files)
    changed = sorted(
        key for key in baseline.files if key in current and baseline.files[key] != current[key]
    )
    removed = sorted(key for key in baseline.files if key not in current)

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
