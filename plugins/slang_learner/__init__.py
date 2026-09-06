# 顶层适配层：把 ego.slang_learner 的实现桥接为顶层插件。
# 不要直接 re-export 实例级属性；实例按需懒初始化。
from plugins.ego.slang_learner import (
    PLUGIN_META,
    SlangLearner,
    start_learning,
    get_pending,
    get_injected,
    get_config,
    set_config,
    inject_selected,
    reject_selected,
)

def _get_learner():
    if not hasattr(_get_learner, "_instance"):
        _get_learner._instance = SlangLearner()
    return _get_learner._instance


PLUGIN_META = dict(PLUGIN_META)
PLUGIN_META["name"] = "slang_learner"
PLUGIN_META["adapter"] = True
PLUGIN_META.setdefault("page", "plugins/slang_learner/ui.html")
