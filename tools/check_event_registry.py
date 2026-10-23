"""事件类型登记检查：出现的事件类型必须已在注册表里。

## 守什么

架构 §8（事件类型登记 · 自研脚本）与 §7.6（**事件类型注册表在冻结区外**：
放 `contracts/`，绝不进 `kernel/` 或 `rules/`）。

注册表是事件类型的**唯一权威**（ADR-020）：任何地方出现一个没登记的事件
类型，都是"内核发出了一条订阅者不认识的事件"——回放校验、完备性检查都会
在它上面踩空。所以这条检查扫的是**整个包**：事件类型出现在哪儿都算数，
权威只有一张表。

## 扫哪里

`src/tetris_kernel/` 全部 `.py` 文件——`contracts/`（声明侧）、`kernel/` /
`rules/`（产出侧）、其余各层（订阅侧）。**扫的不是"哪个目录"，
是"哪个事件类型"**：注册表的权威性不因字面量出现在哪儿而改变。

docstring 不算——那是散文，不是声明。

## 命中之后怎么判（审查修订 L2）

只看 `词:词` 的**形状**会把普通字符串也当成事件类型——契约层出现一个
`"note:todo"`（说明文案、映射键、诊断标签）就被拦，是误报；**误报会让
整个机制失效**。所以分两档：

- **命名空间已登记**（`kernel` / `board` / `piece` …，**从注册表推导**，不另抄
  一份名单）→ 形状对得上却没登记 → **红**：这才是真问题（在既有命名空间下
  私造事件类型）。
- **命名空间未登记** → **警告**：可能是新命名空间的事件，也可能是普通字符串，
  拿不准就别拦——但要说一声。

## 用法

```
python tools/check_event_registry.py
python tools/check_event_registry.py --path src/tetris_kernel/contracts
```

退出码：0 = 全部已登记（可能有警告）；1 = 有未登记的事件类型；2 = 用法或 IO 错误
（**目录不存在也算 2**——「我没扫到」和「我扫了没问题」不能是同一个结果）。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from _toolkit import SRC_ROOT, configure_output, iter_python_files, relative_to_repo

sys.path.insert(0, str(SRC_ROOT))

from tetris_kernel.contracts import EVENT_TYPES  # noqa: E402
from tetris_kernel.contracts.events import namespace_of  # noqa: E402

#: 包根（默认扫描范围）。
PACKAGE_ROOT = SRC_ROOT / "tetris_kernel"

#: 事件类型的形状：`命名空间:名字`（名字可含 `:` 与 `-`），例如
#: `kernel:ready` / `board:lines-cleared` / `kernel:system:installed`。
EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?::[a-z0-9]+(?:-[a-z0-9]+)*)+$")

#: **已登记的事件命名空间**——从注册表推导，不另抄一份名单。
#:
#: 抄一份的后果：注册表加了新命名空间而这张名单没跟着改，新命名空间下私造的
#: 事件类型就会从"红"降级成"警告"（规则与机检悄悄分叉）。
EVENT_NAMESPACES = frozenset(namespace_of(event_type) for event_type in EVENT_TYPES)


@dataclass(frozen=True)
class Finding:
    """一条检查结果。"""

    path: str
    line: int
    event_type: str
    blocking: bool = True


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """docstring 的节点 id（不算声明）。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    ids.add(id(value))
    return ids


def check_file(path: Path) -> list[Finding]:
    """检查一个文件里的事件类型字面量。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = _docstring_nodes(tree)
    rel = relative_to_repo(path)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if not EVENT_TYPE_PATTERN.match(text):
            continue
        if text in EVENT_TYPES:
            continue
        namespace = text.split(":", 1)[0]
        findings.append(
            Finding(
                rel,
                node.lineno,
                text,
                blocking=namespace in EVENT_NAMESPACES,
            )
        )
    return findings


def check_paths(roots: list[Path]) -> tuple[list[Finding], list[Path]]:
    """检查若干目录下的全部 `.py` 文件。

    返回（发现, **缺失的目录**）。目录不存在是 IO 错误，不是"这里没有违规"
    （审查修订 M2）：路径打错就全绿，是守护脚本最坏的失败模式。
    """
    findings: list[Finding] = []
    missing: list[Path] = []
    for root in roots:
        if not root.is_dir():
            missing.append(root)
            continue
        for path in iter_python_files(root):
            findings += check_file(path)
    return findings, missing


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    configure_output()
    parser = argparse.ArgumentParser(description="事件类型登记检查")
    parser.add_argument(
        "--path",
        action="append",
        type=Path,
        default=None,
        help=f"要扫的目录（可重复；默认扫 {relative_to_repo(PACKAGE_ROOT)}）",
    )
    args = parser.parse_args(argv)

    roots = args.path if args.path else [PACKAGE_ROOT]
    print(f"注册表里有 {len(EVENT_TYPES)} 条事件类型。")
    try:
        findings, missing = check_paths(roots)
    except (OSError, SyntaxError) as error:
        print(f"扫描失败：{error}", file=sys.stderr)
        return 2

    for root in missing:
        print(f"目录不存在：{relative_to_repo(root)}")
        print(f"目录不存在：{relative_to_repo(root)}", file=sys.stderr)

    blocking = [finding for finding in findings if finding.blocking]
    warnings = [finding for finding in findings if not finding.blocking]
    for finding in findings:
        mark = "红" if finding.blocking else "警告"
        print(f"{finding.path}:{finding.line}: [{mark}] 未登记的事件类型 {finding.event_type!r}")

    print(f"红 {len(blocking)} 条 · 警告 {len(warnings)} 条")
    if missing:
        print(f"有 {len(missing)} 个目录不存在：没扫到 ≠ 扫过没问题。", file=sys.stderr)
        return 2
    if blocking:
        print(f"事件类型登记检查失败：{len(blocking)} 处未登记。", file=sys.stderr)
        return 1
    print("事件类型登记检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
