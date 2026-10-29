# 分离键清单（H3）

> 来源：`分离与真实-设计.md` §5 白名单 × 2026-10-29 现仓库静态核对。
> 用途：1.6.0 分离启用脚本收尾 grep 闸的核对数据；**只读审计，不改行为**。
> 「分离后可否旁路」= 这些键在 separation 态下可以由 Null* 替身接管/删除，
> 因此启用前必须能被枚举——否则脚本会漏删，留下“人形”残影。

| 分组 | 键 | 读取点（第一个） | 分离后可否旁路 | 备注 |
|---|---|---|---|---|
| 闸 | `reply_question_guard` | `reply_gate.py:61`（共 2 处） | 可旁路 | 可旁路 |
| 闸 | `reply_stale_turns` | `bot_core.py:13260`（共 3 处） | 可旁路 | 可旁路 |
| 闸 | `reply_stale_drop_sec` | `bot_core.py:13245` | 可旁路 | 可旁路 |
| 闸 | `speak_gate_tier` | `speak_gate.py:92`（共 3 处） | 可旁路 | 可旁路 |
| 闸 | `speak_probability` | `speak_gate.py:93` | 可旁路 | 可旁路 |
| 闸 | `speak_rate_max_per_100` | `speak_gate.py:94` | 可旁路 | 可旁路 |
| 闸 | `speak_rate_window_min` | `bot_core.py:959` | 可旁路 | 可旁路 |
| 闸 | `consecutive_reply_limit` | `speak_gate.py:95` | 可旁路 | 可旁路 |
| 闸 | `decider_min_score` | `bot_core.py:12275` | 可旁路 | 可旁路 |
| 量 | `reply_max_chars` | `bot_core.py:13030`（共 2 处） | 可旁路 | 可旁路 |
| 量 | `reply_max_parts` | `bot_core.py:13031`（共 2 处） | 可旁路 | 可旁路 |
| 量 | `reply_max_total` | `bot_core.py:13032`（共 2 处） | 可旁路 | 可旁路 |
| 量 | `reply_long_ratio` | `bot_core.py:12950` | 可旁路 | 可旁路 |
| 量 | `private_reply_interval` | `bot_core.py:360` | 可旁路 | 可旁路 |
| 量 | `group_session_max_replies` | `bot_core.py:379`（共 6 处） | 可旁路 | 可旁路 |
| 量 | `group_msg_buffer_window` | `bot_core.py:392`（共 12 处） | 可旁路 | 可旁路 |
| 量 | `group_msg_buffer_window_session` | `bot_core.py:3605`（共 4 处） | 可旁路 | 可旁路 |
| 量 | `group_msg_buffer_window_trigger` | `bot_core.py:3607`（共 4 处） | 可旁路 | 可旁路 |
| 规则 | `persona_sharpness` | `persona_form.py:101`（共 3 处） | 可旁路 | 可旁路 |
| 规则 | `persona_curiosity` | `persona_form.py:102`（共 3 处） | 可旁路 | 可旁路 |
| 规则 | `persona_warmth` | `persona_form.py:103`（共 3 处） | 可旁路 | 可旁路 |
| 规则 | `persona_talkativeness` | `bot_core.py:12945` | 可旁路 | 可旁路 |
| 规则 | `de_ai.enabled` | `bot_core.py:3864`（共 100 处） | 可旁路 | 可旁路 |
| 规则 | `de_ai.prompt_inject` | `bot_core.py:3865`（共 2 处） | 可旁路 | 可旁路 |
| 规则 | `de_ai.post_process` | `de_ai.py:138` | 可旁路 | 可旁路 |
| 规则 | `de_ai.ban_words` | `de_ai.py:156` | 可旁路 | 可旁路 |
| 规则 | `de_ai.ban_dashes` | `de_ai.py:143` | 可旁路 | 可旁路 |
| 规则 | `de_ai.ban_rule_of_three` | `de_ai.py:161` | 可旁路 | 可旁路 |
| 规则 | `de_ai.ban_filler` | `de_ai.py:151` | 可旁路 | 可旁路 |
| 规则 | `de_ai.ban_sycophancy` | `de_ai.py:146` | 可旁路 | 可旁路 |
| 规则 | `de_ai.max_sentence_len` | `de_ai.py:193` | 可旁路 | 可旁路 |
| 形式 | `quiet_hours` | `bot_core.py:317`（共 17 处） | 可旁路 | 可旁路 |
| 形式 | `group_idle_exit_enable` | `bot_core.py:394` | 可旁路 | 可旁路 |
| 形式 | `group_idle_exit_probability` | `bot_core.py:14122`（共 2 处） | 可旁路 | 可旁路 |
| 形式 | `group_idle_exit_min_silence` | `bot_core.py:403`（共 3 处） | 可旁路 | 可旁路 |
| 形式 | `group_last_exit_cooldown` | `bot_core.py:14122`（共 2 处） | 可旁路 | 可旁路 |
| 形式 | `group_last_exit_cooldown` | `bot_core.py:14122`（共 2 处） | 可旁路 | 可旁路 |
| 形式 | `reply_reaction_delay` | `bot_core.py:12930`（共 6 处） | 可旁路 | 可旁路 |
| 形式 | `private_reply_reaction_delay` | `bot_core.py:15003` | 可旁路 | 可旁路 |

合计 39 个键；无读取点 0 个。

核对备注：设计 §5 写的 group_idle_exit_* 在代码里拆成 group_idle_exit_enable/probability/min_silence + group_last_exit_cooldown；本清单按代码实名登记。
