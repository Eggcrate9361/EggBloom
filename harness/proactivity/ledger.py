# -*- coding: utf-8 -*-
"""兼容壳：会话状态与待回复账本的实现已归位到 `autonomy/ledger.py`。

## 为什么留这个文件

1.6.0 P3 把「心跳 / 账本」收进根下 `autonomy/` 包 —— 它们是自主层的状态，
不该挂在 harness 的观测层里。但老调用点有十几个（`plugins/proactivity`、
`tests/test_proactivity_ledger.py`、`tests/test_proactivity_p3.py`），
全部改导入面太大、也容易漏。所以实现只留**一份**（`autonomy/ledger.py`），
这里做纯转发：

- 模块加载时把实现模块的名字（含 `_job_mode` 这类私有纯函数）铺进本模块全局，
  `from harness.proactivity import ledger as L` 与 `L.scan / L._job_mode` 逐字可用；
- 之后再往实现里加名字，由 `__getattr__` 兜住，不需要同步改这里；
- 不做 `sys.modules` 替换：父包属性仍指向本壳，替换会让
  `from harness.proactivity import ledger` 拿到旧对象。

**不要在这里加逻辑**。要改状态机去改 `autonomy/ledger.py`。
"""
from __future__ import annotations

from autonomy import ledger as _impl

globals().update({k: v for k, v in vars(_impl).items()
                  if not (k.startswith("__") and k.endswith("__"))})


def __getattr__(name):
    return getattr(_impl, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_impl)))
