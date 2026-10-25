"""仓库文本的编码卫生（中文被**编码错配**打散时的守卫）。

## 为什么要有这条

M1 批次 C 收口时实测发现，仓里**三处中文被编码错配打散**：`取值域` 的「值」
成了两个替换字符、`两道门` 的「道」成了三个。**症状是静默的**——文件仍是
**合法 UTF-8**，git 不报错、pytest 不报错、ruff 不报错，**只有人读文档时才发现
少了一个字**。而这几处都在**规格文档**里：「取值域收口」是 M1-C1 的正式说法，
「两道门」是 §5.2 那条发现的结论——**读的人看不出那儿少了个字，只会以为原文
就是这么写的**。

## 为什么以 U+FFFD 为信号

解码失败时的典型产物就是**替换字符**（U+FFFD，逐字节替换）。它是**唯一能从
字节层面认出来的痕迹**：文件本身合法，但内容已经被替换过。

## 为什么只扫版本控制里的文件

用 `git ls-files -z` 取**已跟踪**的文件：NUL 分隔、**路径原样输出**（仓库里有
中文文件名，默认的引号转义会把它们写成 `"..."`，按字面路径去读会读不到）。
只扫**文本**扩展名——PNG 这类二进制里出现同样的字节是正常数据。

## 这条门自己的反向用例

`_scan_for_replacement_chars()` 写成**能对任意文件集合生效**的函数，于是
"它能不能抓住坏字节"可以**常驻**：临时目录里造一个带替换字符的文件，断言
扫描器把它点了名。
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 只扫这些**文本**文件——二进制（PNG）里出现同样的字节是正常数据。
TEXT_SUFFIXES = frozenset(
    {".cfg", ".html", ".ini", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}
)

#: 替换字符 U+FFFD 的 UTF-8 编码——**编码错配写入的典型痕迹**。
REPLACEMENT_CHAR = b"\xef\xbf\xbd"


def _tracked_text_files(root: Path) -> list[Path]:
    """列出**版本控制里**的文本文件。

    **为什么用 `git ls-files -z`**：NUL 分隔、路径原样输出。仓库里有中文文件名
    （`docs/需求与边界.md` 等），默认的引号转义会把它们写成 `"..."`，按字面路径
    去读会**读不到**——扫了 0 个文件还自称通过，是守护脚本最坏的失败模式。
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"git ls-files 失败：{result.stderr.decode(errors='replace')}"

    paths: list[Path] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = root / raw.decode("utf-8")
        if path.suffix in TEXT_SUFFIXES or path.name == ".gitignore":
            paths.append(path)
    return paths


def _scan_for_replacement_chars(paths: Iterable[Path]) -> list[str]:
    """点名**含替换字符**的文件（返回「路径（处数）」）。"""
    offenders: list[str] = []
    for path in paths:
        count = path.read_bytes().count(REPLACEMENT_CHAR)
        if count:
            offenders.append(f"{path}（{count} 处）")
    return offenders


def test_no_tracked_text_file_contains_a_replacement_character() -> None:
    """仓里的文本文件**不许出现替换字符**（U+FFFD）。

    **这条为什么值得存在**：坏字节是**静默**的——文件仍合法、工具全绿，只是
    文档里少了一个字，而且**读的人看不出来**。
    """
    paths = _tracked_text_files(REPO_ROOT)
    assert paths, "扫不到任何文本文件——`git ls-files` 的用法或仓库状态变了"

    offenders = _scan_for_replacement_chars(paths)
    assert not offenders, (
        "这些文件里有被编码错配打散的字符（U+FFFD）："
        f"{offenders}——多半是某次写文件时用了错的编码，请手工把那个字补回去"
    )


def test_the_scanner_names_a_file_whose_bytes_were_corrupted(tmp_path: Path) -> None:
    """**反向用例**：扫描器必须能抓住坏字节（否则上面那条门没有牙）。

    故意写一个带替换字符的文件，断言它**被点了名**且**处数正确**。
    """
    broken = tmp_path / "broken.md"
    broken.write_bytes("取值域收口".encode() + REPLACEMENT_CHAR * 2)

    offenders = _scan_for_replacement_chars([broken])

    assert offenders == [f"{broken}（2 处）"], f"扫描器没抓住坏字节：{offenders}"


def test_the_scanner_keeps_quiet_on_a_clean_file(tmp_path: Path) -> None:
    """**反向用例的反面**：干净文件**不许**被点名（否则这条门只剩噪音）。

    没有这一条，一个"永远返回全部文件"的扫描器也能让上面那条门变绿。
    """
    clean = tmp_path / "clean.md"
    clean.write_bytes("取值域收口".encode())

    assert _scan_for_replacement_chars([clean]) == []
