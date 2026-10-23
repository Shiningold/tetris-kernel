"""内核纯净度检查：禁第三方 import、禁平台名、禁读墙钟。

## 守什么

架构 §4.1（内核「零第三方依赖；禁读墙钟」）、§5.3（内核与规则内不得出现
`time.time()` / `time.monotonic()` / `datetime.now()` / 模块级 `random.*` /
未注入的 `random.Random()` / `os.urandom` / `uuid.uuid4`；`id()` 只警告
不阻断）。

**为什么要有这一层**：`import-linter` 的 forbidden 契约管的是「不准 import
第三方包」，但它**看不见函数调用**——`import time` 是标准库，放行；`time.time()`
才是内核真正不能做的事。这一层专治调用。

## 扫哪里

**冻结区**：`src/tetris_kernel/kernel/` 与 `src/tetris_kernel/rules/`
（架构 §5.3 点名的两个目录）。总线层 `bus/` 不在内——它不读墙钟是另一条
纪律，且第三方 import 已由 `import-linter` 的 forbidden 契约覆盖
（`kernel` / `bus` / `rules` 三层都在那张契约里）。

## 唯一的豁免

**随机源模块 `kernel/rng.py`**：`random.Random(seed)` 只允许出现在那里——
那是**被注入的**随机源（架构 §7.1 的内核随机源）。别处的 `random.Random()`
就是未注入的随机源，红。别处的 `random.random()` 一律红：**模块级随机数
不可播种、不可复现**（G0-3「同种子 10 次全等」会当场翻车）。

`id()` **只警告不阻断**（架构 §5.3 明说）——它不读墙钟，但会让同一状态在
不同进程里长得不一样，值得提一句，不值得拦。

## 写法的变体一律认（审查修订）

只看「属性调用」会漏掉一大片写法，所以这里先把**导入绑定**追出来，再把调用
还原成「模块.原始名」的标准形状：

| 写法 | 还原成 |
| --- | --- |
| `import time` → `time.time()` | `time.time` |
| `import time as t` → `t.time()` | `time.time` |
| `from time import time` → `time()` | `time.time` |
| `import datetime` → `datetime.datetime.now()` | `datetime.datetime.now` |
| `from datetime import datetime` → `datetime.now()` | `datetime.datetime.now` |
| `import random as r` → `r.random()` | `random.random` |

**属性链与别名都要追**：第一版只认 `Name.attr` 形状，于是
`datetime.datetime.now()`（属性链）、`from time import time` 之后的 `time()`、
`import random as r` 之后的 `r.random()` 全都漏网——实测三种都绿。
**守卫要能触发，漏网的守卫等于没守。**

## 用法

```
python tools/check_kernel_purity.py            # 扫冻结区
python tools/check_kernel_purity.py --path src/tetris_kernel/bus   # 另扫一处
```

退出码：0 = 干净（可能有警告）；1 = 有违规；2 = 用法或 IO 错误
（**目录不存在也算 2**——「我没扫到」和「我扫了没问题」不能是同一个结果）。
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from _toolkit import FROZEN_DIRS, configure_output, iter_python_files, relative_to_repo

#: 标准库模块名集合（Python 自带，跨版本稳定）。
STDLIB_MODULES = frozenset(sys.stdlib_module_names)

#: 项目自身的包根——包内导入当然放行。
INTERNAL_ROOT = "tetris_kernel"

#: **随机源模块**（相对 `src/tetris_kernel/`）：`random.Random()` 只允许出现在这里。
RANDOM_SOURCE_MODULE = "kernel/rng.py"

#: 墙钟读的**标准形状**（模块.原始名）。架构 §5.3 点名的全在这儿，另补了同族的
#: `localtime` / `gmtime`（它们读的是墙上时刻）与 `datetime` 的几种取法。
WALL_CLOCK_PATHS = frozenset(
    {
        "time.time",
        "time.time_ns",
        "time.monotonic",
        "time.monotonic_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
        "time.process_time",
        "time.process_time_ns",
        "time.localtime",
        "time.gmtime",
        "os.urandom",
        "uuid.uuid1",
        "uuid.uuid3",
        "uuid.uuid4",
        "uuid.uuid5",
        "uuid.getnode",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.now",
        "datetime.date.today",
        "datetime.now",
        "datetime.today",
    }
)

#: 平台名——出现即说明代码在按操作系统分叉。
#:
#: **只收歧义小的**：`"nt"` / `"posix"` 是 `os.name` 的取值，也是极短的常见词，
#: 内核里出现一个值为 `"nt"` 的普通字符串（模式名、状态缩写）就会被**阻断**。
#: 误报会让整个机制失效，所以这两个**不收**——`os.name` 与 `sys.platform`
#: 的**读取**由属性检查结构性地抓住（见 `PLATFORM_ATTRIBUTES`），不靠字面量。
PLATFORM_NAMES = frozenset(
    {"win32", "linux", "linux2", "darwin", "cygwin", "aix", "freebsd", "sunos"}
)

#: 平台判定入口（属性访问）：`sys.platform` / `os.name`。
PLATFORM_ATTRIBUTES = frozenset({"sys.platform", "os.name"})

#: 导入绑定：本地名 → (来源模块根, 原始名)。原始名为 `None` 表示「这是模块本身」。
Binding = tuple[str, str | None]


@dataclass(frozen=True)
class Finding:
    """一条检查结果。"""

    path: str
    line: int
    category: str
    message: str
    blocking: bool = True


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """收集 docstring 的节点 id——**docstring 不参与字符串字面量检查**。

    docstring 是给人看的散文，里面提到平台名、事件类型都属于说明文字，
    当成违规就是误报。
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    ids.add(id(value))
    return ids


def _import_bindings(tree: ast.AST) -> dict[str, Binding]:
    """建立「本地名 → (来源模块根, 原始名)」的导入绑定表。

    **别名与 from-import 都要追**：只认「模块名 + 属性名」这一个形状的话，
    `import time as t` 之后的 `t.time()`、`from time import time` 之后的 `time()`
    就都成了看不见的调用。
    """
    bindings: dict[str, Binding] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                local = alias.asname or root
                bindings[local] = (root, None)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 包内相对导入
            root = (node.module or "").split(".")[0]
            for alias in node.names:
                bindings[alias.asname or alias.name] = (root, alias.name)
    return bindings


def _dotted_path(node: ast.expr) -> str | None:
    """把属性链还原成点分路径（`datetime.datetime.now` → 字符串）。"""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _canonical_path(path: str, bindings: dict[str, Binding]) -> str:
    """把本地写法还原成「模块.原始名」的标准形状。

    - `t.time()` + `import time as t` → `time.time`
    - `time()` + `from time import time` → `time.time`
    - `datetime.datetime.now()` + `import datetime` → `datetime.datetime.now`
    - `datetime.now()` + `from datetime import datetime` → `datetime.datetime.now`
    """
    head, _, rest = path.partition(".")
    binding = bindings.get(head)
    if binding is None:
        return path
    module_root, original = binding
    if original is None:
        return f"{module_root}.{rest}" if rest else module_root
    tail = f".{rest}" if rest else ""
    return f"{module_root}.{original}{tail}"


def _check_imports(tree: ast.AST, path: str) -> list[Finding]:
    """第三方 import 检查。

    **一条 import 语句里的每个名字都要判**：只判第一个的话，
    `import os, numpy` 会整条漏过（`numpy` 藏在第二个名字里）。
    """
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in STDLIB_MODULES or root == INTERNAL_ROOT:
                    continue
                findings.append(
                    Finding(
                        path,
                        node.lineno,
                        "第三方 import",
                        f"内核只能依赖标准库，发现 {alias.name}",
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 包内相对导入
            root = (node.module or "").split(".")[0]
            if not root or root in STDLIB_MODULES or root == INTERNAL_ROOT:
                continue
            names = "、".join(alias.name for alias in node.names)
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    "第三方 import",
                    f"内核只能依赖标准库，发现 from {node.module} import {names}",
                )
            )
    return findings


def _check_platform_imports(tree: ast.AST, path: str) -> list[Finding]:
    """`platform` 模块本身就不许出现。"""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "platform":
                    findings.append(Finding(path, node.lineno, "平台名", "不得 import platform"))
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "platform":
                findings.append(Finding(path, node.lineno, "平台名", "不得 from platform import …"))
    return findings


def _check_platform_attributes(
    tree: ast.AST, path: str, bindings: dict[str, Binding]
) -> list[Finding]:
    """平台判定属性：`sys.platform` / `os.name`。

    **单独一条**，不挂在调用检查里——`sys.platform == "win32"` 是个比较，
    不是调用；挂在调用上就漏了（守卫要能触发，漏网的守卫等于没守）。
    """
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        path_str = _dotted_path(node)
        if path_str is None:
            continue
        canonical = _canonical_path(path_str, bindings)
        if canonical in PLATFORM_ATTRIBUTES:
            findings.append(
                Finding(path, node.lineno, "平台名", f"不得读 {canonical}（按操作系统分叉）")
            )
    return findings


def _check_calls(
    tree: ast.AST, path: str, bindings: dict[str, Binding], allow_random: bool
) -> list[Finding]:
    """墙钟读、未注入随机源——从**调用点**认，形状先还原再判。"""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        path_str = _dotted_path(node.func)
        if path_str is None:
            continue
        canonical = _canonical_path(path_str, bindings)
        if canonical.startswith("random."):
            if canonical == "random.Random" and allow_random:
                continue  # 被注入的随机源：只许在随机源模块里
            message = (
                "random.Random()：未注入的随机源，只许出现在随机源模块"
                if canonical == "random.Random"
                else f"{canonical}()：模块级随机数不可播种、不可复现"
            )
            findings.append(Finding(path, node.lineno, "随机源", message))
            continue
        if canonical in WALL_CLOCK_PATHS:
            findings.append(Finding(path, node.lineno, "墙钟读", f"不得读墙钟：{canonical}()"))
    return findings


def _check_platform_literals(tree: ast.AST, path: str, docstrings: set[int]) -> list[Finding]:
    """平台名字面量检查（docstring 除外）。"""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        if node.value in PLATFORM_NAMES:
            findings.append(Finding(path, node.lineno, "平台名", f"平台名字面量 {node.value!r}"))
    return findings


def _check_id_calls(tree: ast.AST, path: str) -> list[Finding]:
    """`id()` 只警告不阻断（架构 §5.3）。"""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "id":
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    "id()",
                    "id() 的结果跨进程不稳定，只作提醒，不阻断",
                    blocking=False,
                )
            )
    return findings


def check_file(path: Path, allow_random: bool) -> list[Finding]:
    """检查一个文件。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    rel = relative_to_repo(path)
    bindings = _import_bindings(tree)
    findings: list[Finding] = []
    findings += _check_imports(tree, rel)
    findings += _check_platform_imports(tree, rel)
    findings += _check_platform_attributes(tree, rel, bindings)
    findings += _check_calls(tree, rel, bindings, allow_random)
    findings += _check_platform_literals(tree, rel, _docstring_nodes(tree))
    findings += _check_id_calls(tree, rel)
    return findings


def check_paths(roots: list[Path]) -> tuple[list[Finding], list[Path]]:
    """检查若干目录下的全部 `.py` 文件。

    返回（发现, **缺失的目录**）。目录不存在**不是"这里没有违规"**——
    它是 IO 错误，退出码该是 2；当成"干净"就是把「我没扫到」和「我扫了没问题」
    混成同一个结果，那是最坏的失败模式（审查修订 M2）。
    """
    findings: list[Finding] = []
    missing: list[Path] = []
    for root in roots:
        if not root.is_dir():
            missing.append(root)
            continue
        for path in iter_python_files(root):
            allow_random = relative_to_repo(path).endswith(RANDOM_SOURCE_MODULE)
            findings += check_file(path, allow_random)
    return findings, missing


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    configure_output()
    parser = argparse.ArgumentParser(description="内核纯净度：禁第三方 import / 平台名 / 墙钟读")
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        default=None,
        help="要扫的目录（可重复；默认扫冻结区 kernel/ 与 rules/）",
    )
    args = parser.parse_args(argv)

    roots = args.path if args.path else list(FROZEN_DIRS)
    try:
        findings, missing = check_paths(roots)
    except (OSError, SyntaxError) as error:
        print(f"扫描失败：{error}", file=sys.stderr)
        return 2

    for root in missing:
        # stdout 也打一份：CI 常只收 stdout，只写 stderr 容易看不见。
        print(f"目录不存在：{relative_to_repo(root)}")
        print(f"目录不存在：{relative_to_repo(root)}", file=sys.stderr)

    blocking = [finding for finding in findings if finding.blocking]
    warnings = [finding for finding in findings if not finding.blocking]
    for finding in findings:
        mark = "红" if finding.blocking else "警告"
        print(f"{finding.path}:{finding.line}: [{finding.category}·{mark}] {finding.message}")

    print(
        f"扫了 {len(roots) - len(missing)} 个目录：违规 {len(blocking)} 条"
        f" · 警告 {len(warnings)} 条"
    )
    if missing:
        print(f"有 {len(missing)} 个目录不存在：没扫到 ≠ 扫过没问题。", file=sys.stderr)
        return 2
    if blocking:
        print("内核纯净度检查失败。", file=sys.stderr)
        return 1
    print("内核纯净度检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
