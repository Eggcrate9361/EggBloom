"""meme_engine.py —— 兼容入口（真转发，不是拷贝）。

真正的实现已迁到 `memes/memes/meme_engine.py`（审核状态/黑名单/去重都在那边）。
根目录这份以前是旧副本，绕过了 `_approved_rels`，导致审核区的 pending 图也会被
`match_meme()` 选中发出去。

不再逐个 `dir()` 拷贝属性：拷贝出来的是**另一份模块全局**，调用方改
`meme_engine.MEME_DIR` / `TAGS_FILE` 传不到真模块，旧副本独有的 `reload_index()`
也会凭空消失（`meme_collect.py` 里的调用被 except 静默吞掉，采集后索引没重建）。
这里直接把 `sys.modules["meme_engine"]` 换成真模块本身：读写都是同一个对象，
`from meme_engine import match_meme` 与 `import meme_engine` 拿到的也是同一个。
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

_CANON = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                       "memes", "memes", "meme_engine.py")
_spec = _ilu.spec_from_file_location("_eggbloom_meme_engine", _CANON)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# 导入机制会在 exec 之后重新从 sys.modules 取模块返回，所以这里替换生效。
_mod.__name__ = __name__
_mod.__package__ = __package__
_mod.__file__ = _CANON
_sys.modules[__name__] = _mod
