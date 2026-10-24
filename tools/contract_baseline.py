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
- **冻结面整条增删也红**：规则签名（架构 §7.3.1 的 8 条）与命名结构注册表是
  **v1 冻结面**，整条新增只产生新键——按「新增键放行」会一路绿灯。所以两条
  **计数键**（`count/ruleSignature` · `count/namedStruct`）把条数钉住。
  事件与命令**不加**计数键：架构 §4.4 明确允许新增事件与命令。

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

## 基线文件自己也要可信

「允许新增键」这条口径的前提是**基线文件本身没被动过**。基线一旦被清空或
残缺，「当前有、基线没有」的键会全被当成"新增"放行——**「只增不改」在那一刻
静默退化成「只查已有键」**（批次 D 审查 D1）。所以基线的自述块里记着**它自己
有多少条**（`meta.entryCount`）与**它记的是哪个契约版本**（`meta.contractVersion`）：

- 自述条数与实际条数对不上 → 基线被改过 → 红；
- 基线一条都没有 → 基线被清空 → 红；
- 自述的契约版本与当前契约版本不符 → 红（版本升级 = major 变更）。

**契约版本号是双向对账的**：当前版本升了（基线还记着旧版）与基线里的版本号
被人手改成新版，**两边都红**（批次 D 审查 D6——`meta` 原来只写不读，读的人会
被它误导）。

## 自证块的**边界**：它守什么、不守什么

自述块守的是**基线内部自洽**：条数对不上、版本对不上、基线被清空。

**它守不住「基线被人整份改过」**：少一条键、**同时**把 `meta.entryCount`
改成新条数——基线仍然自洽，工具照样放行（少掉的那条会被当成"新增键"）。
**计数键与被它计数的条目在同一个文件里，自证的上限就是"有人只改了一处"**
（M0 全量审查 M0-1 · 实测复现）。

这是「允许新增键」这条口径的**固有代价**：基线文件自身的可信度最终靠
**git** 保证（改动可见、可回溯），工具只保证"内部不自洽时红"。

**M0 的处置**：**不在 M0 修**——三种修法（跨文件锚定期望值 / 新增键显式
登记 / 少一条即红）都要动架构级口径「允许新增键」或引入新的登记流程，而
契约 v1 已冻结（生成基线的那次提交 `1eae538` 正文写明「契约 v1 冻结」），
解冻的代价与风险不成比例。改为：① 本节把自证强度写实 ② 记进 `PROGRESS.md`
卡点 ③ 在 `docs/plan-M1.md` 留触发点（**M1 第一次新增事件 / 命令时**正好
要拍板这条口径）。

## 刷新要写明理由

`--update-baseline` **必须带 `--reason`**（不给就用法错误退出），并打印
**「旧基线 → 新基线」的差异摘要**：刷新不是无声覆盖，理由与改了什么都要看得见
（批次 D 审查 D4）。

## 用法

```
python tools/contract_baseline.py                    # 校验（默认）
python tools/contract_baseline.py --update-baseline --reason "<理由>"
python tools/contract_baseline.py --checklist-only    # 只看清单状态
python tools/contract_baseline.py --print-entries     # 打印当前条目（排查用）
```

退出码：0 = 通过；1 = 有红项（清单没勾完 / 已有键变化 / 已有键消失 /
基线自身不可信）；2 = 用法或 IO 错误。
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


@dataclass(frozen=True)
class BaselineDocument:
    """读进来的基线文件：条目 + 自述块。

    **自述块不能丢**：它记着"基线自己有多少条"与"基线记的是哪个契约版本"
    ——这两样是基线**自证没被动过**的依据（批次 D 审查 D1 / D6）。
    """

    entries: dict[str, str]
    meta: dict[str, object]


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

    **两条计数键管"整条增删"**：签名与命名结构是 v1 冻结面，整条新增只产生
    新键——按"新增键放行"的口径会一路绿灯（审查修订 M3）。
    """
    entries: list[Entry] = []
    seen: set[str] = set()

    def add(key: str, canonical: str) -> None:
        # 撞键必须当场炸：`entries` 是 list，转 dict 时**后写的会静默顶掉前写的**
        # ——某条声明就这么没进基线，而基线看起来全绿（审查修订 L4）。
        # 结构性键（producer / returns / paramOrder …）与字段名共用一套命名空间，
        # 所以这条断言不是摆设。
        if key in seen:
            raise ValueError(f"基线键重复：{key}")
        seen.add(key)
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

    # -- 结构性计数键：整条增删要红（审查修订 M3） --
    #
    # 签名是 v1 冻结面（架构 §7.3.1 的 **8 条** · ADR-061）：**整条新增一条签名**
    # 只产生新键，按"新增键放行"的口径会一路绿灯放过去——比改参数更大的接口变更
    # 反而更难被发现。命名结构注册表同理（它是形态 9 的展开源，加一个结构就是
    # 加一份契约面）。计数键一变，已有键的哈希就变 → 红。
    #
    # **事件与命令不加计数键**：架构 §4.4 明确「允许新增键（新增字段、事件、
    # 命令）」——新增事件 / 命令是正常成长，不该红；签名与命名结构是**冻结面**，
    # 增删要红。两者口径不同，是有意的。
    add("count/ruleSignature", str(len(RULE_SIGNATURES)))
    add("count/namedStruct", str(len(NAMED_STRUCTS)))

    return entries


# ---------------------------------------------------------------------------
# 基线文件
# ---------------------------------------------------------------------------


def baseline_document(entries: list[Entry]) -> dict[str, object]:
    """基线文件的内容。

    **不写时间戳**：基线文件每次生成都该逐字节一样，否则 git 里全是噪声，
    而噪声会让人对真正的差异视而不见。

    **自述块里记着基线自己有多少条**（`entryCount`）：这是基线**自证没被
    动过**的依据——基线被清空或被删掉几条时，自述块与实际条数对不上，
    基线自己就会报红（批次 D 审查 D1）。
    """
    return {
        "meta": {
            "contractVersion": CONTRACT_VERSION,
            "entryCount": len(entries),
            "hashAlgorithm": HASH_ALGORITHM,
            "generatedBy": "tools/contract_baseline.py",
        },
        "entries": {entry.key: entry.digest for entry in entries},
    }


def load_baseline(path: Path) -> BaselineDocument:
    """读基线文件，返回「条目 + 自述块」。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("entries")
    if not isinstance(entries, dict):
        raise ValueError(f"基线文件结构不对（缺 entries）：{relative_to_repo(path)}")
    meta = document.get("meta")
    if not isinstance(meta, dict):
        raise ValueError(f"基线文件结构不对（缺 meta）：{relative_to_repo(path)}")
    return BaselineDocument(
        entries={str(key): str(value) for key, value in entries.items()},
        meta={str(key): value for key, value in meta.items()},
    )


def baseline_integrity_problems(baseline: BaselineDocument) -> list[str]:
    """基线文件自身不自洽的地方（**每一条都是红项**）。

    **「允许新增键」的前提是基线可信**：基线被清空或残缺时，「当前有、基线
    没有」的键会全被当成"新增"放行——「只增不改」那一刻静默退化成「只查
    已有键」，而工具绿、pytest 绿、文档里的条数照旧写着（批次 D 审查 D1）。

    两条判据：

    - **自述条数与实际条数对账**（`meta.entryCount`）：基线说自己有 N 条，
      实际只有 M 条 → 有人动过它（M < N = 被删了几条，M > N = 被塞了东西）；
    - **契约版本双向对账**（`meta.contractVersion`）：基线记的版本与当前
      `CONTRACT_VERSION` 不符 → 版本升级了而基线没刷新 = major 变更，或
      基线里的版本号被人手改过（批次 D 审查 D6）。
    """
    problems: list[str] = []
    entries = baseline.entries

    if not entries:
        problems.append("基线一条条目都没有：基线被清空了")

    declared = baseline.meta.get("entryCount")
    if not isinstance(declared, int):
        problems.append(f"基线自述块缺 entryCount（现在是 {declared!r}）：基线不是当前口径生成的")
    elif declared != len(entries):
        problems.append(f"基线自述块记着 {declared} 条，实际只有 {len(entries)} 条：基线被改过")

    version = baseline.meta.get("contractVersion")
    if not isinstance(version, str):
        problems.append(f"基线自述块缺 contractVersion（现在是 {version!r}）")
    elif version != CONTRACT_VERSION:
        problems.append(
            f"基线记的契约版本是 {version}，当前是 {CONTRACT_VERSION}：版本升级 = major 变更"
        )

    return problems


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


def _print_update_diff(path: Path, entries: list[Entry], reason: str | None) -> None:
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
    new = {entry.key: entry.digest for entry in entries}
    added = sorted(key for key in new if key not in old.entries)
    changed = sorted(key for key in old.entries if key in new and old.entries[key] != new[key])
    removed = sorted(key for key in old.entries if key not in new)
    print(f"旧基线 {len(old.entries)} 条 → 新基线 {len(new)} 条")
    for key in added:
        print(f"  新增：{key}")
    for key in changed:
        print(f"  变化：{key}（{old.entries[key]} → {new[key]}）")
    for key in removed:
        print(f"  消失：{key}")
    if not (added or changed or removed):
        print("  条目无变化（只有自述块可能变了）")


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
        help="生成 / 刷新基线（清单没勾完就拒绝；必须带 --reason）",
    )
    parser.add_argument(
        "--reason",
        type=str,
        default=None,
        help="刷新基线的理由（--update-baseline 必须带）",
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

    if args.update_baseline and not args.reason:
        parser.error(
            '--update-baseline 必须带 --reason "<理由>"：基线刷新要写明理由，不做无声覆盖。'
        )

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
    except ValueError as error:
        # 键撞了 / 条数对不上——**当场报错，不让它悄悄少一条声明**。
        print(f"契约声明自身有问题，拒绝进基线：{error}", file=sys.stderr)
        return 2

    if args.print_entries:
        for entry in entries:
            print(f"{entry.key}\t{entry.digest}\t{entry.canonical}")
        return 0

    if args.update_baseline:
        if not _report_checklist(items, args.checklist):
            print("拒绝生成基线：冻结清单没勾完。", file=sys.stderr)
            return 1
        _print_update_diff(args.baseline, entries, args.reason)
        write_baseline(args.baseline, entries)
        print(f"已写入基线：{relative_to_repo(args.baseline)}（{len(entries)} 条）")
        print("提醒：生成基线的那次提交，message 里必须写明「契约 v1 冻结」。")
        return 0

    # ---- 默认校验路径 ----
    #
    # **清单门在校验路径上也管**：工具的 docstring 写着"清单没勾完 = 红项"，
    # 那默认路径就必须真的查——否则用户按文档跑第一条命令，看到的是
    # "基线校验通过"，而清单明明缺项（批次 D 审查 D3）。

    if not _report_checklist(items, args.checklist):
        print("基线校验失败：契约 v1 冻结清单没勾完。", file=sys.stderr)
        return 1

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

    problems = baseline_integrity_problems(baseline)
    if problems:
        for problem in problems:
            print(f"基线自身不可信（红）：{problem}")
        print("基线校验失败：基线文件被清空 / 残缺 / 与当前契约版本不符。", file=sys.stderr)
        return 1

    result = compare(baseline.entries, entries)
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
