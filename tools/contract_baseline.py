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
- **冻结面整条增删也红**：规则签名（架构 §7.3.1 的 9 条）与命名结构注册表是
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

**M1 的处置**：`docs/plan-M1.md` 的 S5 落地**第二层防线**（见下节）。**M0 的
判断保留**：契约 v1 已冻结，所以第一层（基线文件里的自述块）**照旧保留**，
两层一起在。

## 第二层防线：跨文件期望清单

第一层守的是**基线内部自洽**：自述条数与实际条数对不上、版本对不上、基线被
清空。它挡不住的是——**计数键与被计数的条目在同一个文件里**：少一条键、
**同时**把 `meta.entryCount` 改成新条数，基线仍然自洽，工具照样放行（少掉的
那条被当成"新增键"）。这是 M0 全量审查 M0-1 的原题，**实测复现过**。

**第二层把期望值移出基线文件**：另有一份期望清单
（`tests/baseline/contracts_expected.json`），两份清单的条目集必须**逐条
相等**——不只是条数相等（删一条 A、加一条 B，条数不变，只比条数照样放行）。
三个维度都看：只在基线里的键 · 只在期望清单里的键 · 同键但哈希不同的。

**这一层防的是"只改一处"，不是防蓄意绕过**：两份都在仓内，**同时改两份仍能
骗过工具**。上限写在这儿，不吹大。

## 第三层防线：git 历史背书（ADR-077）

前两层守的是**文件内部**与**两份文件之间**。它们有一个共同的盲区：**基线
文件本身可以被直接改**——直接编辑基线文件就能让工具变绿，而没人检查这次
编辑是不是走了显式刷新流程。

**第三层把"谁动了这两份文件"交给 git 回答**：契约基线与期望清单**必须与
`git show HEAD:<路径>` 的内容一致**。工作区改了但没提交 → 红；提交了 → 绿。

**它只管那两个文件**（`GIT_BACKED_FILES`）：守卫若盯整个工作区，"改声明、
还没刷基线"的正常流程会被拦下——那是 S1 到 S3 之间的常态。**它守的是
"这两份文件有没有被悄悄改过"，不是"工作区干不干净"。**

**它不区分"谁写的"**，只区分"改了但没提交"与"改了且已提交"：**能把两个文件
改好再提交的人，也能改好 git 历史**——所以这一层是把"静默绕过"变成"留痕
绕过"，**不是"防住了"**（ADR-077 的残余上限照旧写实，不吹大）。

**比对前必须归一化换行符**：本仓库 `core.autocrlf=true` 且没有 `.gitattributes`
——`git show` 给的是 blob（LF），工作区文件是 CRLF，**逐字节比必然永远红**，
守卫会形同虚设。归一化用的是 `normalize_newlines`（与冻结区基线同款，
理由见 `tools/_toolkit.py`）。

## 刷新要写明理由

`--update-baseline` 与 `--update-expected` **都必须带 `--reason`**（不给就用法
错误退出），并打印**「旧基线 → 新基线」的差异摘要**：刷新不是无声覆盖，理由与
改了什么都要看得见（批次 D 审查 D4）。

**两份清单要一起刷**：只刷基线不刷期望清单，跨文件自证立刻变红——这是故意的
（两份都得有人点头），刷新时把 `--update-expected` 一起带上即可。

## M3 新增的两类键

- **`reject/<原因>`**：**每个拒绝原因一条键**。这一类键之前**没有**——拒绝原因
  枚举只被逐命令的 `rejectReasons` 聚合键间接覆盖，于是「往枚举里加一个值」
  不产生任何新键：**「v1 定死 · 只增不改」在基线上没有牙齿**（加了还是全绿）。
  有了它：改名 = 旧键消失（红）· 删值 = 键消失（红）· **新增值 = 新键（放行）**。
- **`system/<名字>`**：**每个已登记系统一条键**，值 = 声明的规范化串。改了订阅集
  或命令集 = **对外承诺变了**，要有牙（spec-M3 §6.4 · FR-9.1 / FR-9.2）。

  **声明的来源是各系统包里的 `SYSTEM_MANIFEST`**（静态扫描）——**不是运行时的
  已登记清单**：基线要的是**确定的**声明集，不能取决于谁装了什么系统。
  **自证在这一层做**：声明里写的事件 / 命令类型必须已登记，写错名字当场拒绝
  进基线（而不是挂一个永远收不到事件的订阅）。

## 用法

```
python tools/contract_baseline.py                    # 校验（默认）
python tools/contract_baseline.py --update-baseline --reason "<理由>"
python tools/contract_baseline.py --update-expected --reason "<理由>"
python tools/contract_baseline.py --checklist-only    # 只看清单状态
python tools/contract_baseline.py --print-entries     # 打印当前条目（排查用）
```

退出码：0 = 通过；1 = 有红项（清单没勾完 / 已有键变化 / 已有键消失 / 基线自身
不可信 / **基线与期望清单不自洽** / **与 `HEAD` 不一致（git 背书）**）；
2 = 用法或 IO 错误。
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
    CONTRACT_EXPECTED,
    HASH_ALGORITHM,
    SRC_ROOT,
    blake2b_hex,
    configure_output,
    git_head_bytes,
    git_repo_root,
    normalize_newlines,
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
    REJECT_REASONS,
    RULE_SIGNATURES,
    SNAPSHOT_FIELDS,
    check_manifest_types,
    manifest_canonical_text,
)
from tetris_kernel.systems.manifests import discover_manifests  # noqa: E402


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

    # -- 拒绝原因枚举 → 每个原因一条键（需求 §8.2 · 只增不改） --
    #
    # **这一类键是 M3 加的**：拒绝原因枚举原本**只被逐命令的 `rejectReasons`
    # 聚合键间接覆盖**——那意味着「往枚举里加一个值」不产生任何新键，
    # **「v1 定死 · 只增不改」这条承诺在基线上没有牙齿**（加一个原因，基线全绿）。
    # 每个原因一条键之后：**改名 = 旧键消失（红）· 删值 = 键消失（红）**，
    # **新增值 = 新键（放行·正常成长）**。
    for reason in REJECT_REASONS:
        add(f"reject/{reason}", reason)

    # -- 系统声明 → 每个已登记系统一条键（spec-M3 §6.4 · FR-9.1 / FR-9.2） --
    #
    # **声明的规范化串进基线**：改了订阅集 / 命令集 = **对外承诺变了**，要有牙。
    # **声明的来源是各系统包里的 `SYSTEM_MANIFEST`**（静态扫描）——**不是运行时
    # 的已登记清单**：基线要的是**确定的**声明集，不能取决于谁装了什么系统。
    #
    # **自证在这一层做**：声明里写的事件 / 命令类型必须已登记，写错名字当场
    # 拒绝进基线（而不是挂一个永远收不到事件的订阅）。
    for system_name, manifest in discover_manifests().items():
        problems = check_manifest_types(
            manifest,
            known_events=EVENT_TYPES.keys(),
            known_commands=COMMANDS.keys(),
        )
        if problems:
            raise ValueError(f"系统 {system_name} 的声明不自证：{'；'.join(problems)}")
        add(f"system/{system_name}", manifest_canonical_text(manifest))

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
    # 签名是 v1 冻结面（架构 §7.3.1 的 **9 条** · ADR-061）：**整条新增一条签名**
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
# 跨文件期望清单（第二层防线）
# ---------------------------------------------------------------------------


@dataclass
class CrossResult:
    """两份清单**逐条**比对的结果。"""

    only_in_baseline: list[str]
    only_in_expected: list[str]
    digest_mismatch: list[tuple[str, str, str]]

    @property
    def failed(self) -> bool:
        """两份清单对不上——红。"""
        return bool(self.only_in_baseline or self.only_in_expected or self.digest_mismatch)


def cross_compare(baseline: dict[str, str], expected: dict[str, str]) -> CrossResult:
    """**逐条**比对两份清单的条目集。

    **不只比条数**：删一条 A、加一条 B，条数不变——只比条数照样放行。三个维度
    都看：只在基线里的键 · 只在期望清单里的键 · 同键但哈希不同的。
    """
    baseline_keys = set(baseline)
    expected_keys = set(expected)
    return CrossResult(
        only_in_baseline=sorted(baseline_keys - expected_keys),
        only_in_expected=sorted(expected_keys - baseline_keys),
        digest_mismatch=[
            (key, baseline[key], expected[key])
            for key in sorted(baseline_keys & expected_keys)
            if baseline[key] != expected[key]
        ],
    )


def expected_document(entries: dict[str, str], version: str) -> dict[str, object]:
    """期望清单文件的内容（形状与基线文件一致：`meta` + `entries`）。

    **期望清单 = 基线文件当前内容的独立副本**——两份分开存，跨文件自证才有
    意义（期望值与计数键同在一处就是 M0-1 的原题）。
    """
    return {
        "meta": {
            "contractVersion": version,
            "entryCount": len(entries),
            "hashAlgorithm": HASH_ALGORITHM,
            "generatedBy": "tools/contract_baseline.py",
        },
        "entries": dict(entries),
    }


def write_expected(path: Path, entries: dict[str, str], version: str) -> None:
    """写期望清单文件（排序输出，保证逐字节可复现）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = expected_document(entries, version)
    text = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# 第三层防线：git 历史背书（ADR-077）
# ---------------------------------------------------------------------------

#: **git 背书守卫视的那两个文件**——**只管这两个**，别的一概不看。
#:
#: 守卫若盯整个工作区，"改声明、还没刷基线"的正常流程会被拦下——那是 S1 到
#: S3 之间的常态。**它守的是"这两份文件有没有被悄悄改过"**，不是"工作区
#: 干不干净"。
GIT_BACKED_FILES: tuple[Path, ...] = (CONTRACT_BASELINE, CONTRACT_EXPECTED)

#: 红项的**机器可辨标记**（ASCII）——测试要在 Windows 的 GBK 管道里断言
#: "这条红项是 git 背书那一层报的"，而中文提示在管道里会被编码错配打散。
#: 标记是 ASCII，按字节查就稳（与 `tests/test_baselines.py` 的 `_run_tool_bytes`
#: 同一手法）。
GIT_BACKING_MARKER = "git-backing"


def git_backing_problem(path: Path) -> str | None:
    """`path` 与 `HEAD` 版本对不上时返回一句红项说明；一致返回 `None`。

    **四种红**（每一种都要说清"为什么"与"怎么办"）：

    - **不在 git 工作区里** → 没有版本历史可对，背书无从谈起；
    - **工作区里文件不见了** → 文件被删了；
    - **`HEAD` 里没有这个文件** → 它从没被提交过；
    - **内容不一致** → 工作区改了但还没提交（或提交漏了它）。

    **只区分"改了但没提交"与"改了且已提交"**，不区分"谁写的"——能把两个
    文件改好再提交的人，也能改好 git 历史。**它是"留痕绕过"不是"防住了"**
    （ADR-077 的残余上限照旧写实）。

    **比对前归一化换行符**：`git show` 给的是 blob（LF），工作区是 CRLF
    （`core.autocrlf=true` 且无 `.gitattributes`）——**不归一化就永远红**。
    """
    repo_root = git_repo_root(path)
    if repo_root is None:
        return f"{path} 不在 git 工作区里：git 背书无从谈起（ADR-077）——这份文件没有版本历史可对"

    try:
        relative = path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return f"{relative_to_repo(path)} 不在仓库 {repo_root} 里：git 背书无从谈起（ADR-077）"

    head = git_head_bytes(repo_root, relative)
    if head is None:
        return f"{relative} 在 HEAD 里不存在（它从没被提交过）：git 背书无从谈起（ADR-077）"

    if not path.exists():
        return (
            f"{relative} 不在工作区里了（文件被删了）：恢复它，"
            "或走显式刷新流程重新生成（--update-baseline --reason / "
            "--update-expected --reason）"
        )

    try:
        current = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return f"{relative} 读不出来：{error}"

    if normalize_newlines(current) == normalize_newlines(head.decode("utf-8", "replace")):
        return None

    return (
        f"{relative} 与 HEAD 的版本不一致（工作区改了但还没提交，或提交漏了它）："
        '走显式刷新流程——--update-baseline --reason "<理由>" / '
        '--update-expected --reason "<理由>"，并与声明改动同笔提交'
    )


def git_backing_problems(paths: tuple[Path, ...] = GIT_BACKED_FILES) -> list[str]:
    """守着那几个文件的 git 背书（每个一条红项说明）。"""
    problems: list[str] = []
    for path in paths:
        problem = git_backing_problem(path)
        if problem is not None:
            problems.append(problem)
    return problems


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
        "--expected",
        type=Path,
        default=CONTRACT_EXPECTED,
        help=f"跨文件期望清单路径（默认 {relative_to_repo(CONTRACT_EXPECTED)}）",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="生成 / 刷新基线（清单没勾完就拒绝；必须带 --reason）",
    )
    parser.add_argument(
        "--update-expected",
        action="store_true",
        help="生成 / 刷新期望清单（照着基线文件当前内容写；必须带 --reason）",
    )
    parser.add_argument(
        "--reason",
        type=str,
        default=None,
        help="刷新基线的理由（--update-baseline / --update-expected 必须带）",
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
    if args.update_expected and not args.reason:
        parser.error(
            '--update-expected 必须带 --reason "<理由>"：期望清单刷新要写明理由，不做无声覆盖。'
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

    if args.update_baseline or args.update_expected:
        # **刷新路径**：两个开关各自都要 --reason（上面已经拒掉没带的了）。
        if args.update_baseline:
            if not _report_checklist(items, args.checklist):
                print("拒绝生成基线：冻结清单没勾完。", file=sys.stderr)
                return 1
            _print_update_diff(args.baseline, entries, args.reason)
            write_baseline(args.baseline, entries)
            print(f"已写入基线：{relative_to_repo(args.baseline)}（{len(entries)} 条）")
            print("提醒：生成基线的那次提交，message 里必须写明「契约 v1 冻结」。")
        if args.update_expected:
            # 期望清单**照着基线文件当前内容写**——它是基线记录的声明集的独立
            # 副本。基线刚刷过就照着新基线写；否则照着盘上的基线文件写。
            if args.update_baseline:
                source = {entry.key: entry.digest for entry in entries}
                version = CONTRACT_VERSION
            else:
                if not args.baseline.exists():
                    print(
                        f"拒绝生成期望清单：基线文件不存在（{relative_to_repo(args.baseline)}）。",
                        file=sys.stderr,
                    )
                    return 2
                try:
                    document = load_baseline(args.baseline)
                except (OSError, ValueError) as error:
                    print(f"读基线失败：{error}", file=sys.stderr)
                    return 2
                source = document.entries
                version = str(document.meta.get("contractVersion", CONTRACT_VERSION))
            write_expected(args.expected, source, version)
            print(f"刷新理由：{args.reason}")
            print(f"已写入期望清单：{relative_to_repo(args.expected)}（{len(source)} 条）")
        if args.update_baseline and not args.update_expected:
            print("提醒：期望清单没跟着刷 → 跨文件自证会红；两份一起刷请同时带 --update-expected。")
        return 0

    # ---- 默认校验路径 ----
    #
    # **清单门在校验路径上也管**：工具的 docstring 写着"清单没勾完 = 红项"，
    # 那默认路径就必须真的查——否则用户按文档跑第一条命令，看到的是
    # "基线校验通过"，而清单明明缺项（批次 D 审查 D3）。

    if not _report_checklist(items, args.checklist):
        print("基线校验失败：契约 v1 冻结清单没勾完。", file=sys.stderr)
        return 1

    # **git 背书（第三层 · ADR-077）**：那两份文件必须与 `HEAD` 一致。
    #
    # **放在比对之前**：**文件的权威性**先于"它与声明对不对得上"——一份没被
    # 提交过的基线，拿它跟声明比对出来的结论没有意义。
    backing_problems = git_backing_problems()
    if backing_problems:
        for problem in backing_problems:
            print(f"git 背书失败（红 · {GIT_BACKING_MARKER}）：{problem}")
        print(
            "基线校验失败：契约基线与期望清单没有与 HEAD 一致（ADR-077）。",
            file=sys.stderr,
        )
        return 1

    baseline: BaselineDocument | None = None
    if args.baseline.exists():
        try:
            baseline = load_baseline(args.baseline)
        except (OSError, ValueError) as error:
            print(f"读基线失败：{error}", file=sys.stderr)
            return 2

    expected: BaselineDocument | None = None
    if args.expected.exists():
        try:
            expected = load_baseline(args.expected)
        except (OSError, ValueError) as error:
            print(f"读期望清单失败：{error}", file=sys.stderr)
            return 2

    if baseline is None:
        if expected is None:
            print(
                f"基线尚未生成：{relative_to_repo(args.baseline)} 不存在"
                "（按计划在 S17 冻结契约 v1 时生成）"
            )
            return 0
        # 期望清单还在 → 基线文件被删了。自述块那一层管不到"文件整个不见"
        # （没有自述块可对账），**跨文件这一层管得到**。
        print(
            f"跨文件自证失败（红）：基线文件不见了"
            f"（{relative_to_repo(args.baseline)}），"
            f"而期望清单还记着 {len(expected.entries)} 条：基线被删了"
        )
        print("基线校验失败：基线与期望清单不自洽。", file=sys.stderr)
        return 1

    problems = baseline_integrity_problems(baseline)
    if problems:
        for problem in problems:
            print(f"基线自身不可信（红）：{problem}")
        print("基线校验失败：基线文件被清空 / 残缺 / 与当前契约版本不符。", file=sys.stderr)
        return 1

    cross_problems: list[str] = []
    if expected is None:
        cross_problems.append(
            f"期望清单不存在：{relative_to_repo(args.expected)}（用 --update-expected 生成）"
        )
    else:
        expected_problems = baseline_integrity_problems(expected)
        for problem in expected_problems:
            print(f"期望清单自身不可信（红）：{problem}")
        if expected_problems:
            print("基线校验失败：期望清单自身不可信。", file=sys.stderr)
            return 1
        cross = cross_compare(baseline.entries, expected.entries)
        for key in cross.only_in_baseline:
            cross_problems.append(f"基线里有、期望清单里没有：{key}")
        for key in cross.only_in_expected:
            cross_problems.append(f"期望清单里有、基线里没有：{key}")
        for key, old, new in cross.digest_mismatch:
            cross_problems.append(f"两份清单的哈希不一致：{key}（基线 {old} ≠ 期望清单 {new}）")

    if cross_problems:
        for problem in cross_problems:
            print(f"跨文件自证失败（红）：{problem}")
        print("基线校验失败：基线与期望清单不自洽。", file=sys.stderr)
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
