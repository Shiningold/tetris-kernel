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

## 用法

```
python tools/check_event_registry.py
python tools/check_event_registry.py --path src/tetris_kernel/contracts
```

退出码：0 = 全部已登记；1 = 有未登记的事件类型；2 = 用法或 IO 错误。
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

#: 包根（默认扫描范围）。
PACKAGE_ROOT = SRC_ROOT / "tetris_kernel"

#: 事件类型的形状：`命名空间:名字`（名字可含 `:` 与 `-`），例如
#: `kernel:ready` / `board:lines-cleared` / `kernel:system:installed`。
EVENT_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?::[a-z0-9]+(?:-[a-z0-9]+)*)+$")


@dataclass(frozen=True)
class Finding:
    """一条检查结果。"""

    path: str
    line: int
    event_type: str


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
        if text not in EVENT_TYPES:
            findings.append(Finding(rel, node.lineno, text))
    return findings


def check_paths(roots: list[Path]) -> list[Finding]:
    """检查若干目录下的全部 `.py` 文件。"""
    findings: list[Finding] = []
    for root in roots:
        if not root.is_dir():
            print(f"目录不存在：{relative_to_repo(root)}", file=sys.stderr)
            continue
        for path in iter_python_files(root):
            findings += check_file(path)
    return findings


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
        findings = check_paths(roots)
    except (OSError, SyntaxError) as error:
        print(f"扫描失败：{error}", file=sys.stderr)
        return 2

    for finding in findings:
        print(f"{finding.path}:{finding.line}: 未登记的事件类型 {finding.event_type!r}")

    if findings:
        print(f"事件类型登记检查失败：{len(findings)} 处未登记。", file=sys.stderr)
        return 1
    print("事件类型登记检查通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
