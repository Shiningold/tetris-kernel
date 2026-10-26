"""适配层：输入（DAS/ARR）· 渲染（tkinter）· 存储。

只做与外界交互的部件，全部降格为系统的一种；不得写内核状态。

## 存储（`storage`）

把字节落成文件的落盘出口。**它不知道默认路径**——路径由调用方给（默认
落盘路径策略是**宿主的装配决定**，ADR-074），也**不读任何环境变量**。
"""

from .storage import TEMP_SUFFIX, FileSink

__all__ = [
    "TEMP_SUFFIX",
    "FileSink",
]
