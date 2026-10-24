"""随机源与时间源测试（S7 · 计划验证方案 1–6）。

覆盖：

1. 同种子两次取 1000 个值，序列**完全相同**；不同种子序列不同。
2. `getstate()` → `setstate()` 后序列接得上（ADR-011）。
3. 状态指纹**跨进程稳定**（子进程算同一状态的指纹并比对相等）——专防
   `hash()` 随机化。
4. 内核纯净度：本次交付的两个模块过 `tools/check_kernel_purity.py` 的检查。
5. `rng:next` 负载**不含** 625 元素的状态数组（负载长度是常数级）。
6. 扁平化状态**无损往返**：`export()` → `import_state()` 后序列接得上；
   `gauss_next` 非 `None` 时导出**报错**。

另附：只用两个原语的守卫（ADR-010）、7-bag 的洗牌性质、时钟的两种实现。

批次 A 的测试文件**自给自足**：不依赖 `tests/conftest.py` 的 fixture。
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tetris_kernel.contracts import EVENT_TYPES
from tetris_kernel.host.clock import Clock, RealClock, VirtualClock
from tetris_kernel.kernel.fingerprint import blake2b_hex, tlv_digest
from tetris_kernel.kernel.rng import RNG_NEXT_EVENT, KernelRng

#: 仓库根目录（本文件在 `tests/` 下）。
REPO_ROOT = Path(__file__).resolve().parents[1]

#: 本次交付的两个内核模块（纯净度检查就查这两个）。
RNG_MODULE = REPO_ROOT / "src" / "tetris_kernel" / "kernel" / "rng.py"
FINGERPRINT_MODULE = REPO_ROOT / "src" / "tetris_kernel" / "kernel" / "fingerprint.py"

#: 纯净度检查脚本在 `tools/` 下，**它自己** import 同目录的 `_toolkit`，
#: 所以要把 `tools/` 挂到 `sys.path` 上。
sys.path.insert(0, str(REPO_ROOT / "tools"))
_purity = importlib.import_module("check_kernel_purity")
_toolkit = importlib.import_module("_toolkit")

#: ADR-010 否决的取数方式——内核随机源一个都不许用。
FORBIDDEN_PRIMITIVES = frozenset(
    {
        "shuffle",
        "sample",
        "choice",
        "randrange",
        "randint",
        "gauss",
        "gauss_next",
        "normalvariate",
        "uniform",
        "triangular",
        "weibullvariate",
        "vonmisesvariate",
        "paretovariate",
        "lognormvariate",
        "expovariate",
        "gammavariate",
        "betavariate",
    }
)

#: 7-bag 的标准七种方块（**测试自备**：内核不认方块名，那是规则层的事）。
PIECES = ("I", "J", "L", "O", "S", "T", "Z")


# ---------------------------------------------------------------------------
# 验证 1：同种子同序列 · 不同种子不同序列
# ---------------------------------------------------------------------------


def test_same_seed_produces_identical_sequence() -> None:
    """同种子两次取 1000 个值，序列完全相同。"""
    first = KernelRng(20261024)
    second = KernelRng(20261024)
    left = [first.next_float() for _ in range(1000)]
    right = [second.next_float() for _ in range(1000)]
    assert left == right


def test_different_seed_produces_different_sequence() -> None:
    """不同种子 → 不同序列。"""
    left = [KernelRng(1).next_float() for _ in range(1000)]
    right = [KernelRng(2).next_float() for _ in range(1000)]
    assert left != right


def test_bag_shuffle_is_reproducible_permutation() -> None:
    """7-bag 洗牌：同种子同结果，且袋里每种方块各一个。"""
    first = KernelRng(7)
    second = KernelRng(7)
    bag_a = first.next_bag(PIECES)
    bag_b = second.next_bag(PIECES)
    assert bag_a == bag_b
    assert sorted(bag_a) == sorted(PIECES)
    assert len(set(bag_a)) == len(PIECES)


def test_bag_shuffle_differs_between_seeds() -> None:
    """不同种子的洗牌结果不同（不是恒等排列）。"""
    bags = {KernelRng(seed).next_bag(PIECES) for seed in range(20)}
    assert len(bags) > 1


# ---------------------------------------------------------------------------
# 验证 2：getstate() / setstate() 往返
# ---------------------------------------------------------------------------


def test_getstate_setstate_roundtrip_continues_sequence() -> None:
    """`getstate()` → `setstate()` 后序列接得上。"""
    source = KernelRng(11)
    target = KernelRng(11)
    for _ in range(10):
        source.next_float()
        target.next_float()

    target.setstate(source.getstate())

    assert [source.next_float() for _ in range(100)] == [target.next_float() for _ in range(100)]


# ---------------------------------------------------------------------------
# 验证 3：状态指纹跨进程稳定（专防 hash() 随机化）
# ---------------------------------------------------------------------------


def _fingerprint_in_subprocess(seed: int, draws: int, hash_seed: str) -> str:
    """在子进程里算同一个状态的指纹，返回十六进制串。"""
    script = (
        "from tetris_kernel.kernel.rng import KernelRng\n"
        f"rng = KernelRng({seed})\n"
        f"for _ in range({draws}):\n"
        "    rng.next_float()\n"
        "print(rng.state_fingerprint())\n"
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return completed.stdout.strip()


def test_state_fingerprint_is_stable_across_processes() -> None:
    """不同 `PYTHONHASHSEED` 的子进程算出的指纹与本进程相等。

    内置 `hash()` 会在这一步翻车（`PYTHONHASHSEED` 随机化 → 跨进程必变）。
    """
    in_process = KernelRng(4242)
    for _ in range(17):
        in_process.next_float()
    expected = in_process.state_fingerprint()

    assert _fingerprint_in_subprocess(4242, 17, "0") == expected
    assert _fingerprint_in_subprocess(4242, 17, "12345") == expected


def test_fingerprint_uses_blake2b_not_builtin_hash() -> None:
    """状态指纹就是 blake2b-16 的小写 hex（32 个字符）。"""
    rng = KernelRng(3)
    fingerprint = rng.state_fingerprint()
    assert len(fingerprint) == 32
    assert fingerprint == fingerprint.lower()
    assert set(fingerprint) <= set("0123456789abcdef")


def test_blake2b_hex_matches_toolkit_algorithm() -> None:
    """内核侧的 blake2b 与工具层同算法、同摘要长度（`tools/_toolkit.py` 收
    `str` 并做 UTF-8 编码，内核侧收 `bytes`——参数类型按 plan §1.4.4 的
    先验定签名走，算法与摘要长度必须一致）。"""
    assert _toolkit.HASH_SIZE == 16
    assert _toolkit.HASH_ALGORITHM == "blake2b-16"
    payload = b"tetris"
    assert blake2b_hex(payload) == _toolkit.blake2b_hex(payload.decode("utf-8"))


def test_tlv_digest_is_order_sensitive_and_rejects_duplicate_keys() -> None:
    """TLV：顺序由调用方定（函数原样保留），重复键报错。"""
    first = tlv_digest((("a", b"1"), ("b", b"2")))
    second = tlv_digest((("b", b"2"), ("a", b"1")))
    assert first != second
    assert tlv_digest((("a", b"1"), ("b", b"2"))) == first
    with pytest.raises(ValueError):
        tlv_digest((("a", b"1"), ("a", b"2")))


def test_tlv_digest_distinguishes_key_boundaries() -> None:
    """键与值都带长度 → `("ab", b"c")` 与 `("a", b"bc")` 不撞（ADR-026）。"""
    assert tlv_digest((("ab", b"c"),)) != tlv_digest((("a", b"bc"),))


# ---------------------------------------------------------------------------
# 验证 4：内核纯净度（只查本次交付的两个模块）
# ---------------------------------------------------------------------------


def test_delivered_kernel_modules_pass_purity_check() -> None:
    """`kernel/rng.py` 与 `kernel/fingerprint.py` 过纯净度检查。

    **只查这两个文件**——冻结区里还有别的写手正在写的文件，全目录扫描会把
    他们的半成品算到我头上。
    """
    findings = []
    for path in (RNG_MODULE, FINGERPRINT_MODULE):
        allow_random = path.name == "rng.py"
        findings += _purity.check_file(path, allow_random)
    blocking = [finding for finding in findings if finding.blocking]
    assert blocking == [], [f"{f.path}:{f.line} {f.message}" for f in blocking]


def test_rng_module_uses_only_the_two_primitives() -> None:
    """ADR-010：`shuffle` / `sample` / `choice` / `randrange` 一个都不许用。"""
    tree = ast.parse(RNG_MODULE.read_text(encoding="utf-8"))
    used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert used & FORBIDDEN_PRIMITIVES == set()


def test_random_instance_is_only_created_in_rng_module() -> None:
    """`random.Random(seed)` 只出现在随机源模块里（未注入的随机源 = 红）。

    **按 AST 数代码里的调用点**，不数文本——docstring 里提到 `random.Random`
    不算调用。
    """
    tree = ast.parse(RNG_MODULE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Random"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "random"
    ]
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 验证 5：rng:next 负载不含完整 MT 状态
# ---------------------------------------------------------------------------


def test_rng_next_payload_is_constant_size() -> None:
    """负载只有三个字段，且不含 625 元素的状态数组（ADR-060）。"""
    rng = KernelRng(20261024)
    rng.next_float()
    drafts = rng.take_drafts()
    assert len(drafts) == 1

    fields = drafts[0].payload.fields()
    assert tuple(name for name, _ in fields) == ("value", "seq", "stateFingerprint")
    assert len(fields) == 3

    # 状态数组本身有 625 个元素——负载里一个都没有。
    state_length = len(rng.getstate()[1])
    assert state_length == 625
    for name, value in fields:
        if isinstance(value, tuple):
            assert len(value) <= 4, f"{name} 里塞了长数组"

    # 指纹是短哈希（32 个 hex 字符），不是状态本体。
    fingerprint = dict(fields)["stateFingerprint"]
    assert len(fingerprint) == 32


def test_payload_size_stays_constant_over_many_draws() -> None:
    """取值再多，负载长度仍是常数级。"""
    rng = KernelRng(5)
    for _ in range(200):
        rng.next_float()
    drafts = rng.take_drafts()
    assert len(drafts) == 200
    for draft in drafts:
        assert len(draft.payload.fields()) == 3


def test_rng_next_event_type_is_registered() -> None:
    """事件类型名必须与 `contracts/events.py` 的注册表一致（注册表在
    `contracts/`，不在 `kernel/`）。"""
    assert RNG_NEXT_EVENT in EVENT_TYPES
    assert EVENT_TYPES[RNG_NEXT_EVENT].namespace == "rng"


def test_seq_increments_and_fingerprint_is_state_after_draw() -> None:
    """`seq` 逐次递增；状态指纹取「该次取值之后」的状态。"""
    rng = KernelRng(99)
    rng.next_float()
    rng.next_int(8)
    drafts = rng.take_drafts()

    assert [draft.payload.seq for draft in drafts] == [0, 1]
    assert drafts[0].payload.stateFingerprint != drafts[1].payload.stateFingerprint

    # 取完不再动状态时，最后一条草稿的指纹 == 当前状态指纹。
    rng.next_float()
    last = rng.take_drafts()[-1]
    assert last.payload.stateFingerprint == rng.state_fingerprint()


def test_take_drafts_clears_the_queue() -> None:
    """`take_drafts()` 取走即清空（不重复发同一条草稿）。"""
    rng = KernelRng(8)
    rng.next_float()
    assert len(rng.take_drafts()) == 1
    assert rng.take_drafts() == ()


# ---------------------------------------------------------------------------
# 验证 6：扁平化状态无损往返
# ---------------------------------------------------------------------------


def test_flat_state_roundtrip_continues_sequence() -> None:
    """`export()` → `import_state()` 后序列接得上。"""
    source = KernelRng(20261024)
    target = KernelRng(20261024)
    for _ in range(5):
        source.next_float()

    flat = source.export()
    assert len(flat) == 626
    assert all(isinstance(value, int) for value in flat)

    target.import_state(flat)
    assert [source.next_float() for _ in range(50)] == [target.next_float() for _ in range(50)]


def test_flat_state_roundtrip_is_lossless() -> None:
    """往返后导出的扁平状态逐项相等（无损）。"""
    source = KernelRng(31)
    for _ in range(3):
        source.next_float()
    flat = source.export()

    target = KernelRng(0)
    target.import_state(flat)
    assert target.export() == flat


def test_export_raises_when_gauss_next_is_not_none() -> None:
    """`gauss_next` 非 `None` 时导出**报错**（不报错等于静默丢状态）。"""
    rng = KernelRng(1)
    version, state, _gauss_next = rng.getstate()
    rng.setstate((version, state, 0.5))

    with pytest.raises(RuntimeError):
        rng.export()


def test_import_state_rejects_wrong_length() -> None:
    """长度不对的扁平状态直接报错（不静默塞进内核）。"""
    rng = KernelRng(1)
    with pytest.raises(ValueError):
        rng.import_state((3, 1, 2, 3))


def test_import_state_rejects_version_mismatch() -> None:
    """版本对不上直接报错（ADR-011：混用会**静默重放出不同结果**）。

    导出的状态带着自己的 `version`；导入时必须**用导入的那个**复原，而不是
    当前实例的——否则版本检测被绕过去，跨 Python 版本的旧录像会安静地重放出
    另一局。
    """
    rng = KernelRng(7)
    flat = rng.export()
    tampered = (flat[0] + 1, *flat[1:])

    with pytest.raises(ValueError, match="版本不匹配"):
        rng.import_state(tampered)


def test_import_state_uses_the_imported_version() -> None:
    """复原用的 version 来自**导入的状态**，不是当前实例的。

    版本对得上时，导入后的状态与导出的状态逐位相等。
    """
    source = KernelRng(11)
    for _ in range(5):
        source.next_float()
    flat = source.export()

    target = KernelRng(0)
    target.import_state(flat)

    assert target.export() == flat


# ---------------------------------------------------------------------------
# 时钟源（宿主层）：真实 / 虚拟同一接口
# ---------------------------------------------------------------------------


def test_virtual_clock_only_moves_when_advanced() -> None:
    """虚拟时钟：外部显式推进。"""
    clock = VirtualClock(start_ms=0, step_ms=16)
    assert clock.now_ms() == 0
    assert clock.advance() == 16
    assert clock.now_ms() == 16
    assert clock.advance(3) == 64
    assert clock.frames == 4
    assert clock.step_ms() == 16


def test_real_and_virtual_clocks_share_one_interface() -> None:
    """真实 / 虚拟是同一接口的两种实现。"""
    assert isinstance(RealClock(), Clock)
    assert isinstance(VirtualClock(), Clock)

    real = RealClock(step_ms=20)
    assert real.step_ms() == 20
    assert real.now_ms() >= 0
    assert real.now_ms() <= real.now_ms()


def test_clock_rejects_invalid_step_and_frames() -> None:
    """步长必须为正、帧数不能为负。"""
    with pytest.raises(ValueError):
        RealClock(step_ms=0)
    with pytest.raises(ValueError):
        VirtualClock(step_ms=-1)
    with pytest.raises(ValueError):
        VirtualClock().advance(-1)
