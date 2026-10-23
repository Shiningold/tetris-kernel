"""内核层：唯一的状态所有者。

只接受命令、只发出事件；对外接口只有 execute / advance_frame / snapshot。
不 import 总线、零第三方依赖、禁读墙钟（架构 §7.1、§5.3）。
"""
