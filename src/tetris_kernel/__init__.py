"""Tetris Kernel：以内核为中心的俄罗斯方块底座。

分层依赖方向恒为 host > adapters > systems > bus > kernel > rules > contracts，
由 lint-imports 持续校验。内核与总线零第三方依赖（ADR-003）。
"""
