"""存储适配器：把字节落成文件（ADR-064 · M2-D2 · ADR-085）。

## 它只管"怎么写下去"，不管"写到哪儿"

**路径由调用方给**——默认落盘路径策略是**宿主的装配决定**（ADR-074：
"路径策略属于宿主装配层（`host/`），**不是** `systems/replay/` 的一部分"）。
本模块**没有**"默认路径"这个概念，也**不读任何环境变量**。

**部件自己不 `open()` / 不用 `Path.home()`**（ADR-064）：落盘只走**宿主注入
的 sink**，本模块就是那个 sink 的一种实现。

## 两种写入语义（**不是一个**）

| 实现 | 语义 | 谁用 |
| --- | --- | --- |
| `FileSink` | **覆盖**：每次调用把整个文件替换成给定的字节 | 录像（**整份**交出） |
| `AppendSink` | **追加**：每次调用把字节**接在文件末尾** | 日志条目 · 指标采样（**每条一行**） |

**为什么必须分成两个**：两种产物的**写法**根本不同——录像是"局末把整份交
出来"，日志与指标是"一条一条往外冒"。**用覆盖语义写日志，文件里只剩最后
一条**；**用追加语义写录像，文件里会攒着一份份残缺的录像**（M2 审查 H1）。

**一个文件一个产物**：日志、指标、录像**各有各的出口**——同一个文件被两个
出口写，**后写的覆盖先写的**（"写入只有一处"说的是**一条落盘路径**，不是
"一个文件" · ADR-085）。

## 为什么"先写临时文件再原子替换"

**半截文件比没有文件更坏**：录像写到一半被中断，磁盘上留下一个**看起来
完整**的录像——重放时读它会拿到截断的内容，而"文件存在"这件事会骗过
"录像齐了吗"的检查（`G2-1` 的四项全等会拿它当基准，比出假红）。

`os.replace` 在同一文件系统内是**原子**的：要么是完整的旧内容，要么是完整
的新内容，**没有中间态**。临时文件与目标同目录，所以替换也在同一文件系统
内（跨文件系统的 `replace` 会退化成"复制 + 删除"，那就不是原子了）。

**追加没有这个保证**：进程被强杀时，文件末尾可能留**半行**。日志流能接受
（**丢的是最后一条日志**，不是整份产物），**录像不能**——所以录像用
`FileSink`（覆盖 + 原子替换），**两者的保证不同，不混用**。

**文件名不带墙钟**（ADR-064）：临时文件是目标文件名加一个固定后缀，
不带时间戳——带时间戳会让"同一局录两次"产生两份不同的文件。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 写入过程中的临时文件后缀（**不是**时间戳——ADR-064：文件名不带墙钟）。
TEMP_SUFFIX = ".part"


class FileSink:
    """把字节写到文件的落盘出口（`Callable[[bytes], None]` 的一种实现）。

    **覆盖语义**：每次调用把整个文件替换成给定的字节。录像就是这种用法
    （ADR-030：内存 list 累积，局末**一次性**落盘）。

    **不追加**：追加是另一种语义（见 `AppendSink`）——**两种语义是两个类**，
    不靠参数切换：**用错了就是数据丢**，那不如让类型把这件事说清楚。

    **目录自建**：父目录不存在时**连父目录一起建**（ADR-074）。
    """

    def __init__(self, path: Path) -> None:
        """建一个落到 `path` 的落盘出口。

        **不创建任何目录**——建目录是写入时的事（解析路径不该有副作用）。
        """
        self._path = Path(path)
        self._written = 0

    @property
    def path(self) -> Path:
        """目标文件路径。"""
        return self._path

    @property
    def bytes_written(self) -> int:
        """已写入的字节数（**累计**，覆盖写也照加——指标与测试要用）。"""
        return self._written

    def __call__(self, data: bytes) -> None:
        """把 `data` 写进目标文件（**原子替换**）。

        **失败不静默**：目录建不出来、磁盘满、权限不足——**一律当场抛**，
        不吞掉。吞掉的话，"录像没落盘"会变成"录像齐了"（ADR-064 的口径）。
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + TEMP_SUFFIX)
        temp.write_bytes(data)
        os.replace(temp, self._path)
        self._written += len(data)

    def __repr__(self) -> str:
        """调试用的表示（**不含墙钟**）。"""
        return f"FileSink({self._path!s})"


class AppendSink:
    """把字节**追加**到文件的落盘出口（`Callable[[bytes], None]` 的一种实现）。

    **追加语义**：每次调用把字节**接在文件末尾**——文件里已有的内容一条不少。
    日志条目与指标采样就是这种用法（**每条一行**）。

    **与 `FileSink` 的分工**（ADR-085）：

    | | 语义 | 谁用 |
    | --- | --- | --- |
    | `FileSink` | 覆盖（整份交出） | 录像 |
    | `AppendSink` | 追加（一条一条） | 日志 · 指标 |

    **为什么不是一个类的两种模式**：写错了就是**数据丢**（用覆盖写日志，文件
    里只剩最后一条）——**让类型把这件事说清楚**，比让人记住参数强。

    **追加没有原子性保证**：进程被强杀时，文件末尾可能留**半行**。日志流能
    接受（丢的是最后一条），**录像不能**——所以录像用 `FileSink`。

    **目录自建**：父目录不存在时**连父目录一起建**（ADR-074）。
    """

    def __init__(self, path: Path) -> None:
        """建一个落到 `path` 的落盘出口。

        **不创建任何目录**——建目录是写入时的事（解析路径不该有副作用）。
        """
        self._path = Path(path)
        self._written = 0

    @property
    def path(self) -> Path:
        """目标文件路径。"""
        return self._path

    @property
    def bytes_written(self) -> int:
        """已写入的字节数（**累计**）。"""
        return self._written

    def __call__(self, data: bytes) -> None:
        """把 `data` **追加**到目标文件末尾。

        **失败不静默**：目录建不出来、磁盘满、权限不足——**一律当场抛**
        （与 `FileSink` 同款口径 · ADR-064）。
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("ab") as handle:
            handle.write(data)
        self._written += len(data)

    def __repr__(self) -> str:
        """调试用的表示（**不含墙钟**）。"""
        return f"AppendSink({self._path!s})"


__all__ = [
    "TEMP_SUFFIX",
    "AppendSink",
    "FileSink",
]
