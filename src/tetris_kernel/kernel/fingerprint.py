"""确定性指纹：blake2b + TLV（ADR-026 · 架构 §5.4 · plan-M0.md S7 口径 3）。

内核侧的稳定哈希工具。**与 `tools/_toolkit.py` 的 `blake2b_hex` 算法一致**
（blake2b / 16 字节摘要 / 小写 hex），但**另起一份**——`tools/` 不随包发布，
`src/` 也不该 import 脚本层。

## 为什么不用内置 `hash()`

`str` / `bytes` 的内置 `hash()` 受 `PYTHONHASHSEED` 随机化影响，**跨进程必变**：
同一份状态在两个进程里算出两个不同的指纹，指纹比对会毫无规律地失败。
指纹是回放校验的依据（架构 §5.4 的四项比对），必须跨进程稳定
（ADR-026 / ADR-060）。

## TLV 记录长什么样

一条记录 = **键**（4 字节小端长度 + UTF-8 字节）+ **值**（4 字节小端长度 +
字节）；整条输入前加域分隔前缀 `tlv/v1`。

**键也带长度前缀**，所以 `("ab", b"c")` 与 `("a", b"bc")` 不会撞——ADR-026
点名的三类歧义（tuple / None / 元素边界）由此一次性解决。

**值的类型标签由调用方负责**：本模块只收字节，不解释值的含义，所以
bool / int / float / None 的区分编码写在调用方（同一个域内必须一致）。
"""

from __future__ import annotations

import hashlib

#: 摘要长度（字节）。16 字节 = 32 个十六进制字符，够用且短。
HASH_SIZE = 16

#: 哈希算法名（与 `tools/_toolkit.py` 的 `HASH_ALGORITHM` 一致）。
HASH_ALGORITHM = f"blake2b-{HASH_SIZE}"

#: 编码格式的版本标记（TLV 编码格式本身的标记，不是业务域）。
#:
#: **业务域的分隔由调用方负责**——本模块只给一套编码格式，不同业务域（棋盘内容
#: / 扩展槽 / 随机源状态）要得到互不相撞的摘要，调用方得自己带域标签：写进条目
#: 的**值**里（`kernel/snapshot.py` 的 `_domain_tag` 就是这么做的），或用互不
#: 重名的条目键。**写进键里会给键加限制**——扩展槽的 key 是宿主定的任意字符串。
DOMAIN_PREFIX = b"tlv/v1"


def blake2b_hex(payload: bytes) -> str:
    """稳定哈希（blake2b / 16 字节 / 小写 hex）。

    参数是**字节**而不是字符串：编码由调用方决定，避免"隐式 UTF-8"这层
    隐式约定在两处悄悄分叉（工具层那份收 `str` 并自己做 UTF-8 编码，
    算法与摘要长度与此一致）。
    """
    return hashlib.blake2b(payload, digest_size=HASH_SIZE).hexdigest()


def tlv_digest(items: tuple[tuple[str, bytes], ...]) -> str:
    """按 TLV 编码取摘要（blake2b / 16 字节 / 小写 hex）。

    ## 键的顺序：**由调用方决定，本函数原样保留传入顺序**

    顺序是调用方的语义——棋盘是行优先、`extensions` 按 key 排序——函数擅自
    排序会把「第 10 行」排到「第 2 行」前面，那比不排序更坏（**静默换序**）。
    要规范化（同一个 key 集合永远得到同一个摘要），调用方自己先排序。

    ## 记录格式

    `域分隔前缀 + Σ(键长度(4B 小端) + 键字节 + 值长度(4B 小端) + 值字节)`

    键与值都带长度，所以元素边界无歧义；重复键直接报错——同一个键出现两次，
    多半是调用方漏了唯一性校验（快照的 `extensions` 就有这条要求）。

    ## 值的类型标签由调用方负责

    本函数只收字节，不解释值的含义：bool / int / float / None 的区分编码
    写在调用方（ADR-026 的「布尔与整数区分编码」），但**同一个域内必须一致**。
    """
    seen: set[str] = set()
    buffer = bytearray(DOMAIN_PREFIX)
    for key, value in items:
        if key in seen:
            raise ValueError(f"TLV 键重复：{key!r}")
        seen.add(key)
        key_bytes = key.encode("utf-8")
        buffer += len(key_bytes).to_bytes(4, "little")
        buffer += key_bytes
        buffer += len(value).to_bytes(4, "little")
        buffer += value
    return blake2b_hex(bytes(buffer))


__all__ = [
    "DOMAIN_PREFIX",
    "HASH_ALGORITHM",
    "HASH_SIZE",
    "blake2b_hex",
    "tlv_digest",
]
