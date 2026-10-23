"""规则层：消行判定 / 重力 / 计分 / 边界四项可插拔规则 + 形状表与踢墙表。

冻结区（kernel/ + rules/），规则表落成 Python 字面量常量（ADR-032）。
接口只用中性类型，不 import kernel 的任何 dataclass（架构 §7.3）。
"""
