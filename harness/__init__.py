# -*- coding: utf-8 -*-
"""EggBloom harness —— 有意保留的「干净 / 顶级决策 / 超高效 / 低成本」基建。

设计依据：docs/plan/dsh-harness-design.md（§7.3 输出保留、§7.4 spill、§11.1 P5/P6）。

⚠️ 研究里有一条重要警告：「从最小工具集起步；模型变强后，复杂脚手架的收益下降
而维护成本不降」。所以本包**只放经过验证的高收益原语**，不做大而全的框架。

当前内容：
  - ItemRetainer : 保留前 N 个有序逻辑单元，**精确省略计数**
  - TextRetainer : **按字节** 保留 head/tail/headTail，内存有界，切割处修剪不完整码点
  - spill        : 超预算内容落盘 + 内联首尾预览 + 绝对路径 + 读取提示
  - session_log  : 事件溯源会话日志（任务 5.3 试点：仅追加 + 从日志派生 prompt）
  - time_ctx     : 时间上下文分级注入（粗粒度靠前 / 精粒度靠尾，保前缀缓存）
  - tool_registry: 工具按需加载 + 固定序列化顺序 + MCP 命名空间（省 token 的关键）
  - skill_loader : Skill 三级渐进披露（L1 元数据常驻 / L2 触发时载入 / L3 按需读文件）
  - mcp_hub      : MCP 最小实现（stdio + server__ 命名空间 + 惰性枚举 + 缓存 tools/list）
  - tool_runner  : 工具执行的**单点实现**（主回复链与 agent 共用，避免语义分裂）
  - retry_policy : LLM 调用的**重试策略**（错误分类 + 指数退避 + 抖动 + Retry-After）
  - freshness    : **时效性护栏**（知识截止差距 + 时效敏感判定 + 结果过期标注）
  - workspace    : 工作区**安全边界**（路径解析单点实现；越界/候选/人话错误）
  - tool_fs      : 工作型文件工具（ws_read/ws_edit/ws_glob/ws_grep/ws_write/ws_edit_many）
  - tool_proc    : 持久 Python 会话与后台任务（py_run/job_*）+ 任务清单（todo_write）
  - agent_loop   : Agent 运行时内核（主程序 agent 能力：两入口共用）
  - agent_service: Agent 服务层（注入依赖 + 会话管理 + 群内双闸）
  - agent_memory : Agent 本体记忆桥（nexsandglass + super_memory，不另起一套）
  - agent_project: EggBloom 项目知识层（常驻速查 + 文档索引 + 铁律 + 工程模式）
  - bot_hook     : bot 主回复链薄接入点（时间分级 + 动态 tools + skill 元数据）

设计参考：docs/plan/harness-research-findings.md
"""
from .retention import ItemRetainer, TextRetainer, format_retention_notice
from .spill import spill_text, SPILL_DIR
from . import session_log
from . import time_ctx
from . import tool_registry
from . import skill_loader
from . import mcp_hub
from . import tool_runner
from . import retry_policy
from . import freshness
from . import workspace
from . import tool_fs
from . import tool_proc
from . import agent_loop
from . import agent_service
from . import agent_memory
from . import agent_project
from . import bot_hook
from . import plugin_bridge

__all__ = [
    "ItemRetainer",
    "TextRetainer",
    "format_retention_notice",
    "spill_text",
    "SPILL_DIR",
    "session_log",
    "time_ctx",
    "tool_registry",
    "skill_loader",
    "mcp_hub",
    "tool_runner",
    "retry_policy",
    "freshness",
    "workspace",
    "tool_fs",
    "tool_proc",
    "agent_loop",
    "agent_service",
    "agent_memory",
    "agent_project",
    "bot_hook",
    "plugin_bridge",
]
