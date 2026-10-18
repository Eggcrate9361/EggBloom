"""
NexSandglass 路径配置 — 单一真相来源 V2.3
===========================================
所有模块从这里获取 _NB，不再各自计算。
用法: from sandglass_paths import _NB, _SCRIPTS, _PERSONA, ...

v2.3 变更：数据根改为**优先放在程序目录旁**（可移植），用户主目录降为回退。
原因见 _resolve_nb() 的注释。
"""

import os

__version__ = "2.9.10"


def _app_root() -> str:
    """程序工作根（resources/app）。本文件在 <app>/nexsandglass/ 下。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_nb() -> str:
    """解析 NeuroBase 数据根。

    顺序与理由：
      1. `NEXSANDBASE_HOME` 环境变量 —— 显式指定，永远最优先
      2. `<程序目录>/.neurobase` —— 存在就用它
      3. `~/.neurobase` —— 老安装的数据还在这儿，继续可用
      4. 都没有 → 建在**程序目录旁**（新装默认就是可移植的）；
         程序目录不可写（只读介质 / 权限不足）才退回主目录

    为什么把程序目录排在主目录前面：PORTABLE_STRUCTURE.md 承诺
    「整个文件夹可以复制到任意路径、任意电脑直接运行」。
    NeuroBase 的记忆如果留在 ~/.neurobase，这句话就是假的 ——
    把文件夹拷到另一台电脑，沙漏、影子灵魂、决策粒子会静默全部丢失。
    """
    env = os.environ.get("NEXSANDBASE_HOME")
    if env:
        return env

    app_root = _app_root()
    portable = os.path.join(app_root, ".neurobase")
    home = os.path.join(os.path.expanduser("~"), ".neurobase")

    if os.path.isdir(portable):
        return portable
    if os.path.isdir(home):
        return home
    # 全新安装：能写程序目录就建成可移植的，否则退回主目录
    if os.access(app_root, os.W_OK):
        return portable
    return home


_NB = _resolve_nb()

_SCRIPTS = os.path.join(_NB, "scripts")
_PERSONA = os.path.join(_NB, "persona")
_ARCHIVE = os.path.join(_NB, "archive")

# 常用文件路径
_SANDGLASS = os.path.join(_NB, "sandglass.txt")
_SANDGLASS_DB = os.path.join(_NB, "sandglass.db")
_SANDGLASS_IDX = os.path.join(_NB, "sandglass.idx")
_SHADOW_DB = os.path.join(_NB, "shadow_sand.db")
_DECISION_PARTICLES = os.path.join(_NB, "decision_particles.txt")
_DECISION_VOCAB = os.path.join(_NB, "decision_vocab.txt")
_ECHO_WIND = os.path.join(_NB, "echo_wind.jsonl")
_EMOTION_VOCAB = os.path.join(_NB, "emotion_vocab.jsonl")
_IRON_RULES = os.path.join(_NB, "iron_rules.txt")

# 启动时必须存在的目录
_REQUIRED_DIRS = [_NB, _SCRIPTS, _PERSONA, _ARCHIVE]


def validate() -> dict:
    """启动时路径验证——创建缺失目录，返回状态报告。

    如果首选数据根建不出来（只读介质、权限不足），这里会**退回主目录**并
    重算路径常量；因为这发生在启动早期，各模块尚未 import _NB，
    不会出现"一半模块用新路径一半用旧路径"的错位。
    """
    global _NB, _SCRIPTS, _PERSONA, _ARCHIVE, _SANDGLASS, _SANDGLASS_DB
    global _SANDGLASS_IDX, _SHADOW_DB, _DECISION_PARTICLES, _DECISION_VOCAB
    global _ECHO_WIND, _EMOTION_VOCAB, _IRON_RULES, _REQUIRED_DIRS

    created, existed, fell_back = [], [], False
    for d in _REQUIRED_DIRS:
        try:
            if not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
                created.append(d)
            else:
                existed.append(d)
        except OSError:
            # 建不出来就整体退到主目录，避免半新半旧
            home = os.path.join(os.path.expanduser("~"), ".neurobase")
            if os.path.normcase(home) == os.path.normcase(_NB):
                raise
            _NB = home
            _SCRIPTS = os.path.join(_NB, "scripts")
            _PERSONA = os.path.join(_NB, "persona")
            _ARCHIVE = os.path.join(_NB, "archive")
            _SANDGLASS = os.path.join(_NB, "sandglass.txt")
            _SANDGLASS_DB = os.path.join(_NB, "sandglass.db")
            _SANDGLASS_IDX = os.path.join(_NB, "sandglass.idx")
            _SHADOW_DB = os.path.join(_NB, "shadow_sand.db")
            _DECISION_PARTICLES = os.path.join(_NB, "decision_particles.txt")
            _DECISION_VOCAB = os.path.join(_NB, "decision_vocab.txt")
            _ECHO_WIND = os.path.join(_NB, "echo_wind.jsonl")
            _EMOTION_VOCAB = os.path.join(_NB, "emotion_vocab.jsonl")
            _IRON_RULES = os.path.join(_NB, "iron_rules.txt")
            _REQUIRED_DIRS = [_NB, _SCRIPTS, _PERSONA, _ARCHIVE]
            fell_back = True
            for d2 in _REQUIRED_DIRS:
                if not os.path.exists(d2):
                    os.makedirs(d2, exist_ok=True)
                    created.append(d2)
                else:
                    existed.append(d2)
            break

    return {"nb": _NB, "created": created, "existed": existed,
            "fell_back": fell_back, "ok": True}


def where() -> dict:
    """诊断用：说清数据根是怎么定下来的。"""
    app_root = _app_root()
    return {
        "nb": _NB,
        "version": __version__,
        "env_override": os.environ.get("NEXSANDBASE_HOME") or "",
        "portable_path": os.path.join(app_root, ".neurobase"),
        "portable_exists": os.path.isdir(os.path.join(app_root, ".neurobase")),
        "home_path": os.path.join(os.path.expanduser("~"), ".neurobase"),
        "home_exists": os.path.isdir(os.path.join(os.path.expanduser("~"), ".neurobase")),
        "is_portable": os.path.normcase(_NB) == os.path.normcase(
            os.path.join(app_root, ".neurobase")),
    }
