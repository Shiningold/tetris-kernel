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

## 用法

```
python tools/check_kernel_purity.py            # 扫冻结区
python tools/check_kernel_purity.py --path src/tetris_kernel/bus   # 另扫一处
```

退出码：0 = 干净（可能有警告）；1 = 有违规；2 = 用法或 IO 错误。
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

#: 墙钟读的函数名（按模块分组）。架构 §5.3 点名的全在这儿，另补了同族的
#: `localtime` / `gmtime`（它们读的是墙上时刻）。
WALL_CLOCK_FUNCTIONS: dict[str, frozenset[str]] = {
    "time": frozenset(
        {
            "time",
            "time_ns",
            "monotonic",
            "monotonic_ns",
            "perf_counter",
            "perf_counter_ns",
            "process_time",
            "process_time_ns",
            "localtime",
            "gmtime",
        }
    ),
    "datetime": frozenset({"now", "utcnow", "today"}),
    "os": frozenset({"urandom"}),
    "uuid": frozenset({"uuid1", "uuid3", "uuid4", "uuid5", "getnode"}),
}

#: 平台名——出现即说明代码在按操作系统分叉。
PLATFORM_NAMES = frozenset(
    {"win32", "linux", "linux2", "darwin", "cygwin", "nt", "posix", "aix", "freebsd"}
)

#: 平台判定入口（属性访问）：`sys.platform` / `os.name`。
PLATFORM_ATTRIBUTES = frozenset({("sys", "platform"), ("os", "name")})


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


def _module_of_import(node: ast.Import | ast.ImportFrom) -> str:
    """取导入的根模块名（相对导入返回空串）。"""
    if isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            return ""  # 包内相对导入
        return (node.module or "").split(".")[0]
    return node.names[0].name.split(".")[0]


def _check_imports(tree: ast.AST, path: str) -> list[Finding]:
    """第三方 import 检查。"""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        root = _module_of_import(node)
        if not root:
            continue
        if root in STDLIB_MODULES or root == INTERNAL_ROOT:
            continue
        line = getattr(node, "lineno", 0)
        name = getattr(node, "names", [None])[0]
        detail = getattr(name, "name", "") if name is not None else ""
        findings.append(
            Finding(path, line, "第三方 import", f"内核只能依赖标准库，发现 {detail or root}")
        )
    return findings


def _check_platform_imports(tree: ast.AST, path: str) -> list[Finding]:
    """`platform` 模块本身就不许出现。"""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        if _module_of_import(node) != "platform":
            continue
        findings.append(Finding(path, getattr(node, "lineno", 0), "平台名", "不得 import platform"))
    return findings


def _check_platform_attributes(tree: ast.AST, path: str) -> list[Finding]:
    """平台判定属性：`sys.platform` / `os.name`。

    **单独一条**，不挂在调用检查里——`sys.platform == "win32"` 是个比较，
    不是调用；挂在调用上就漏了（守卫要能触发，漏网的守卫等于没守）。
    """
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if (node.value.id, node.attr) in PLATFORM_ATTRIBUTES:
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    "平台名",
                    f"不得读 {node.value.id}.{node.attr}（按操作系统分叉）",
                )
            )
    return findings


def _check_calls(tree: ast.AST, path: str, allow_random: bool) -> list[Finding]:
    """墙钟读、平台判定、未注入随机源——都从**调用点**认。"""
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            continue
        module = func.value.id
        attr = func.attr
        if module == "random":
            if attr == "Random" and allow_random:
                continue  # 被注入的随机源：只许在随机源模块里
            findings.append(
                Finding(
                    path,
                    node.lineno,
                    "随机源",
                    f"random.{attr}()：模块级随机数不可播种、不可复现"
                    if attr != "Random"
                    else "random.Random()：未注入的随机源，只许出现在随机源模块",
                )
            )
            continue
        banned = WALL_CLOCK_FUNCTIONS.get(module)
        if banned and attr in banned:
            findings.append(Finding(path, node.lineno, "墙钟读", f"不得读墙钟：{module}.{attr}()"))
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
    findings: list[Finding] = []
    findings += _check_imports(tree, rel)
    findings += _check_platform_imports(tree, rel)
    findings += _check_platform_attributes(tree, rel)
    findings += _check_calls(tree, rel, allow_random)
    findings += _check_platform_literals(tree, rel, _docstring_nodes(tree))
    findings += _check_id_calls(tree, rel)
    return findings


def check_paths(roots: list[Path]) -> list[Finding]:
    """检查若干目录下的全部 `.py` 文件。"""
    findings: list[Finding] = []
    for root in roots:
        if not root.is_dir():
            print(f"目录不存在：{relative_to_repo(root)}", file=sys.stderr)
            continue
        for path in iter_python_files(root):
            allow_random = relative_to_repo(path).endswith(RANDOM_SOURCE_MODULE)
            findings += check_file(path, allow_random)
    return findings


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
        findings = check_paths(roots)
    except (OSError, SyntaxError) as error:
        print(f"扫描失败：{error}", file=sys.stderr)
        return 2

    blocking = [finding for finding in findings if finding.blocking]
    warnings = [finding for finding in findings if not finding.blocking]
    for finding in findings:
        mark = "红" if finding.blocking else "警告"
        print(f"{finding.path}:{finding.line}: [{finding.category}·{mark}] {finding.message}")

    print(f"扫了 {len(roots)} 个目录：违规 {len(blocking)} 条 · 警告 {len(warnings)} 条")
    if blocking:
        print("内核纯净度检查失败。", file=sys.stderr)
        return 1
    print("内核纯净度检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
