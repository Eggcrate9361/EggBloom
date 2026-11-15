#    Group Chat Decider（EggBloom_v2  ）

##   （    ）

**        ，        **     
        model call，         ：
1. **    ** —   Bot    
2. **   ** —   /   /  /  /  
3. **   ** —   /  /  /   /   /  

    EggBloom_v2   ：
- **set_ai_client(ai)** —      main.py   AI  
- **try-import** —               ，  slang_injector   
- **to_prompt_injection()** —        system_prompt

##    （EggBloom_v2）

### 1.    （ `main.py`    import  ， slang_injector ）

```python
#               
try:
    from group_chat_decider import decide as group_decide, set_ai_client as group_set_ai
    group_set_ai(ai)  #   main.py   ai
    GROUP_DECIDER_OK = True
    print("[GroupDecider]     ")
except Exception as e:
    group_decide = lambda msg, gid, ctx: None
    GROUP_DECIDER_OK = False
    print(f"[GroupDecider]   : {e}")
```

### 2. `process_buffered_message`   （  `reply = call_deepseek(...)`  ）

```python
# ——     ：    （@     ）——
if GROUP_DECIDER_OK and not is_at_bot:
    decision = group_decide(merged_text, group_id, {
        "recent_msgs": list(GROUP_RECENT_MSGS.get(group_id, []))[-5:],
        "bot_replies": GROUP_BOT_REPLIES.get(group_id, []),
    })
    if decision is not None:
        if decision.should_reply is False:
            log(f"  [{group_id}] [  ]  : {decision.reason}")
            return
        #      system_prompt
        sys_prompt += decision.to_prompt_injection()
```

`system_prompt`       ：

```
【   】   （  0.9）
【   】  ——   AI  ，    
```

LLM   ，            「[   ]」

### 3.      （`process_buffered_message`  ）

```python
reply = call_deepseek(merged_text, [], group_id=group_id, is_group=True)
```

>    try-import   ，      try-import  ，
>                 。

##  

 `group_chat_decider`   ，EggBloom_v2    ：

```bash
# EggBloom_v2    （   main.py   ）
xcopy /E /I <group_chat_decider路径> <EggBloom_v2>\group_chat_decider\
```

##  

```bash
cd <group_chat_decider路径>
python test_decider.py
```

    AI   ：
```bash
set DEEPSEEK_API_KEY=your_key
python test_decider.py
```

##  

 `config.py`：

|   |   |  |
|------|--------|------|
| ENABLED | True |    |
| MAX_TOKENS | 800 |    token（1.5.10 E3：README 旧值 200，代码已是 800） |
| TEMPERATURE | 0.3 |     |
| CONFIDENCE_THRESHOLD | 0.3 |    1.5.10 E3：README 旧值 0.6，代码已是 0.3 |
| MAX_RECENT_MSGS | 5 |     |

##  

  set_ai_client        EggBloom_v2   `ai`（AIClient   ），
  API    。

##  

|  |  |
|------|--------|
| prompt | ~150 tokens |
| completion | ~200 tokens |
|   | ~350 tokens |
| DeepSeek   | ~0.00015  /  |

##  

 `<group_chat_decider路径>\`   ，      try-import    ，
     。

##  

- `__init__.py` —     （  decide, Decision, set_ai_client）
- `config.py` —    
- `decider.py` —     （    AI Client）
- `prompt_templates.py` — prompt  
- `test_decider.py` —