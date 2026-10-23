"""契约基线：`contracts/` 的「只增不改」（架构 §4.4 / §7.6 · 契约清单第 11 / 12 项）。

## 两道机制，一道门

**门（拒绝生成）**：基线**首次生成前必须先过契约 v1 冻结清单**
（`docs/契约v1清单.md`）——清单有未勾项就**拒绝生成**并打印缺哪几项。
没有这道门，「勾完才允许生成」只是一句道德劝告（架构 §7.6）。
清单自身**不进基线**：它是过程文档，进基线会自锁（清单一变基线就红）。

**牙齿（只增不改）**：基线按**字段级**哈希——键 = 字段名（事件类型名 / 命令
类型名 / 快照字段名 / 规则签名参数名 …），值 = 该声明的哈希。

- **允许新增键**：新增快照字段、新增事件、新增命令是正常的成长，不该红。
- **禁止已有键的哈希变化**：改既有字段的类型 = major 变更。
- **已有键消失也红**：改名 = 删旧键 + 加新键，只看「新增」会把它当成纯新增
  ——**成员表是从声明推导的，改名会被误判**。所以删键必须报出来。

## 为什么基线要按字段级而不是压成一坨

把整张快照压成一个哈希值，将来加一个字段就撞红——**明明是新增却判成 major**，
误报会让整个机制失效（架构 §4.4）。

## 哈希的是什么

**声明，不是值**（架构 §4.4）：信封结构、事件负载字段、命令参数规格、快照
字段类型、规则签名进；序号、帧号、负载的具体值一律不进。

哈希的是**声明处引用的类型串**，且**命名结构引用按成员表展开**
（文法附加规则第 4 条）——`Board` 代表 `BOARD_FIELDS` 那张表，改了成员表，
引用它的快照字段的哈希也跟着变。共用取值域常量（`types.CELLS` 等）是**定义**，
不进基线；引用处展开后的类型串进——**常量改了、引用没改，两处静默分叉的
后果正是基线要抓的**。

## 用法

```
python tools/contract_baseline.py                    # 校验（默认）
python tools/contract_baseline.py --update-baseline   # 生成 / 刷新基线
python tools/contract_baseline.py --checklist         # 只看清单状态
python tools/contract_baseline.py --print-entries     # 打印当前条目（排查用）
```

退出码：0 = 通过；1 = 有红项（清单没勾完 / 已有键变化 / 已有键消失）；2 = 用法或 IO 错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from _toolkit import (
    CONTRACT_BASELINE,
    CONTRACT_CHECKLIST,
    HASH_ALGORITHM,
    SRC_ROOT,
    blake2b_hex,
    configure_output,
    relative_to_repo,
)
from contract_grammar import GrammarError, canonicalize

sys.path.insert(0, str(SRC_ROOT))

from tetris_kernel.contracts import (  # noqa: E402
    COMMANDS,
    CONTRACT_VERSION,
    ENVELOPE_FIELDS,
    EVENT_TYPES,
    METADATA_KEYS,
    NAMED_STRUCTS,
    RULE_SIGNATURES,
    SNAPSHOT_FIELDS,
)


@dataclass(frozen=True)
class ChecklistItem:
    """冻结清单的一行。"""

    number: int
    title: str
    checked: bool


@dataclass(frozen=True)
class Entry:
    """基线的一条：键 + 规范化声明 + 哈希。"""

    key: str
    canonical: str
    digest: str


# ---------------------------------------------------------------------------
# 冻结清单
# ---------------------------------------------------------------------------


def _plain(text: str) -> str:
    """去掉 Markdown 强调符号，留下人能读的标题。"""
    return text.replace("**", "").replace("`", "").strip()


def parse_checklist(path: Path) -> list[ChecklistItem]:
    """解析契约 v1 冻结清单的表体行。

    认的是「`|` 分隔的表格行，编号是数字」；状态列里有 `☑` 算勾了。

    **解析不出 16 项就报错**，不当作"全勾了"——清单读不出来时静默放行，
    等于把门拆了（守卫要能触发，恒真的守卫等于没守）。
    """
    text = path.read_text(encoding="utf-8")
    items: list[ChecklistItem] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        items.append(ChecklistItem(int(cells[0]), _plain(cells[1]), "☑" in cells[-1]))
    numbers = {item.number for item in items}
    if numbers != set(range(1, 17)):
        raise ValueError(
            f"冻结清单解析异常：认出 {sorted(numbers)}，应当是 1–16 各一项"
            f"（文件：{relative_to_repo(path)}）"
        )
    return sorted(items, key=lambda item: item.number)


def missing_checklist_items(items: list[ChecklistItem]) -> list[ChecklistItem]:
    """没勾的清单项。"""
    return [item for item in items if not item.checked]


# ---------------------------------------------------------------------------
# 声明条目
# ---------------------------------------------------------------------------


def collect_entries() -> list[Entry]:
    """收集契约面的全部声明条目。

    **键 = 字段名（事件类型名 / 命令类型名 / 快照字段名 / 签名参数名 …）**，
    值 = 该声明规范化后的哈希。

    覆盖四部分（架构 §7.6）：事件类型 → 负载字段；命令类型 → 参数规格；
    快照字段 → 类型；规则接口签名。另带信封结构、元数据键、命名结构成员表
    与几条**结构性的**声明（门禁归属、拒绝原因、参数顺序、生产者、命名空间）。

    **结构性的那几条是刻意加的**：参数顺序、门禁归属、拒绝原因、生产者这些
    改动不体现在任何单个字段的类型串上，不单独钉住就等于没进基线。
    """
    entries: list[Entry] = []

    def add(key: str, canonical: str) -> None:
        entries.append(Entry(key=key, canonical=canonical, digest=blake2b_hex(canonical)))

    def add_type(key: str, type_text: str) -> None:
        add(key, canonicalize(type_text, NAMED_STRUCTS))

    # -- 信封结构与元数据键（契约清单第 1、2 项） --
    for field in ENVELOPE_FIELDS:
        add_type(f"envelope/{field.name}", field.type)
    for field in METADATA_KEYS:
        add_type(f"metadata/{field.name}", field.type)

    # -- 事件类型 → 负载字段 + 产出者 + 命名空间（契约清单第 7、8 项） --
    for event_type, spec in EVENT_TYPES.items():
        for field in spec.payload:
            add_type(f"event/{event_type}/{field.name}", field.type)
        add(f"event/{event_type}/producer", spec.producer)
        add(f"event/{event_type}/namespace", spec.namespace)

    # -- 命令类型 → 参数规格 + 门禁归属 + 拒绝原因（契约清单第 5、6 项） --
    for command_name, spec in COMMANDS.items():
        for arg in spec.args:
            add_type(f"command/{command_name}/{arg.name}", arg.type)
        add(f"command/{command_name}/lifecycleGated", "true" if spec.lifecycle_gated else "false")
        add(f"command/{command_name}/rejectReasons", ",".join(spec.reject_reasons))

    # -- 快照字段 → 类型（契约清单第 3 项） --
    for field_name, field in SNAPSHOT_FIELDS.items():
        add_type(f"snapshot/{field_name}", field.type)

    # -- 规则接口签名 → 参数 + 返回类型 + 参数顺序（契约清单第 9 项） --
    for signature_name, signature in RULE_SIGNATURES.items():
        for param in signature.params:
            add_type(f"rule/{signature_name}/{param.name}", param.type)
        add_type(f"rule/{signature_name}/returns", signature.returns)
        # 参数顺序即调用顺序（架构 §7.3.1）——重排必须被看见。
        add(
            f"rule/{signature_name}/paramOrder",
            ",".join(param.name for param in signature.params),
        )

    # -- 命名结构成员表（形态 9 的展开源） --
    for struct_name, members in NAMED_STRUCTS.items():
        for member_name, member_type in members.items():
            add_type(f"struct/{struct_name}/{member_name}", member_type)

    return entries


# ---------------------------------------------------------------------------
# 基线文件
# ---------------------------------------------------------------------------


def baseline_document(entries: list[Entry]) -> dict[str, object]:
    """基线文件的内容。

    **不写时间戳**：基线文件每次生成都该逐字节一样，否则 git 里全是噪声，
    而噪声会让人对真正的差异视而不见。
    """
    return {
        "meta": {
            "contractVersion": CONTRACT_VERSION,
            "hashAlgorithm": HASH_ALGORITHM,
            "generatedBy": "tools/contract_baseline.py",
        },
        "entries": {entry.key: entry.digest for entry in entries},
    }


def load_baseline(path: Path) -> dict[str, str]:
    """读基线文件，返回「键 → 哈希」。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"基线文件结构不对（缺 entries）：{relative_to_repo(path)}")
    return {str(key): str(value) for key, value in entries.items()}


def write_baseline(path: Path, entries: list[Entry]) -> None:
    """写基线文件（排序输出，保证逐字节可复现）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = baseline_document(entries)
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """校验结果。"""

    added: list[Entry]
    changed: list[tuple[str, str, str]]
    removed: list[str]

    @property
    def failed(self) -> bool:
        return bool(self.changed or self.removed)


def compare(baseline: dict[str, str], entries: list[Entry]) -> CheckResult:
    """比对基线与当前声明。

    - 新增键 → 放行（新增字段 / 事件 / 命令是正常成长）。
    - 已有键哈希变化 → 红（改既有声明 = major 变更）。
    - 已有键消失 → 红（改名 = 删旧键 + 加新键，只看新增会把它当成纯新增）。
    """
    current = {entry.key: entry for entry in entries}
    added = [entry for key, entry in current.items() if key not in baseline]
    changed = [
        (key, baseline[key], current[key].digest)
        for key in baseline
        if key in current and baseline[key] != current[key].digest
    ]
    removed = [key for key in baseline if key not in current]
    return CheckResult(added=added, changed=changed, removed=removed)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def _report_checklist(items: list[ChecklistItem], path: Path) -> bool:
    """打印清单状态，返回「是否全部勾完」。"""
    missing = missing_checklist_items(items)
    if not missing:
        print(f"契约 v1 冻结清单：16 项全勾（{relative_to_repo(path)}）")
        return True
    print(f"契约 v1 冻结清单未勾完，缺 {len(missing)} 项：")
    for item in missing:
        print(f"  第 {item.number} 项：{item.title}")
    return False


def _print_result(result: CheckResult) -> None:
    for entry in result.added:
        print(f"新增键（放行）：{entry.key}")
    for key, old, new in result.changed:
        print(f"已有键哈希变化（红）：{key}  基线 {old} ≠ 当前 {new}")
    for key in result.removed:
        print(f"已有键消失（红）：{key}  —— 改名或删除 = major 变更")


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    configure_output()
    parser = argparse.ArgumentParser(description="契约基线：只增不改 + 冻结清单门禁")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=CONTRACT_BASELINE,
        help=f"基线文件路径（默认 {relative_to_repo(CONTRACT_BASELINE)}）",
    )
    parser.add_argument(
        "--checklist",
        type=Path,
        default=CONTRACT_CHECKLIST,
        help="契约 v1 冻结清单路径",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="生成 / 刷新基线（清单没勾完就拒绝）",
    )
    parser.add_argument(
        "--checklist-only",
        action="store_true",
        help="只跑清单校验并打印缺项",
    )
    parser.add_argument(
        "--print-entries",
        action="store_true",
        help="打印当前声明条目（键 + 规范化串 + 哈希），排查用",
    )
    args = parser.parse_args(argv)

    try:
        items = parse_checklist(args.checklist)
    except (OSError, ValueError) as error:
        print(f"读冻结清单失败：{error}", file=sys.stderr)
        return 2

    if args.checklist_only:
        return 0 if _report_checklist(items, args.checklist) else 1

    try:
        entries = collect_entries()
    except GrammarError as error:
        print(f"契约声明不符合类型文法，拒绝进基线：{error}", file=sys.stderr)
        return 2

    if args.print_entries:
        for entry in entries:
            print(f"{entry.key}\t{entry.digest}\t{entry.canonical}")
        return 0

    if args.update_baseline:
        if not _report_checklist(items, args.checklist):
            print("拒绝生成基线：冻结清单没勾完。", file=sys.stderr)
            return 1
        write_baseline(args.baseline, entries)
        print(f"已写入基线：{relative_to_repo(args.baseline)}（{len(entries)} 条）")
        print("提醒：生成基线的那次提交，message 里必须写明「契约 v1 冻结」。")
        return 0

    if not args.baseline.exists():
        print(
            f"基线尚未生成：{relative_to_repo(args.baseline)} 不存在"
            "（按计划在 S17 冻结契约 v1 时生成）"
        )
        return 0

    try:
        baseline = load_baseline(args.baseline)
    except (OSError, ValueError) as error:
        print(f"读基线失败：{error}", file=sys.stderr)
        return 2

    result = compare(baseline, entries)
    _print_result(result)
    print(
        f"条目 {len(entries)} 条：新增 {len(result.added)} · 变化 {len(result.changed)}"
        f" · 消失 {len(result.removed)}"
    )
    if result.failed:
        print("基线校验失败：已有键的哈希变化或消失 = major 变更。", file=sys.stderr)
        return 1
    print("基线校验通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
