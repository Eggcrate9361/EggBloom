# -*- coding: utf-8 -*-
"""
group_chat_decider - independent test
       ： main.py   ：
    1. set_ai_client(ai)      AI
    2.  try-import decide    ，
      system_prompt   【  】【   】
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decider import decide, Decision
from prompt_templates import build_prompt
from config import ENABLED


def test_build_prompt():
    """  prompt """
    print("=" * 50)
    print("[  ] prompt ")
    print("=" * 50)

    message = "   DeepSeek API   ？    "
    recent_msgs = [
        "   ",
        "     ",
        "   AI  ",
    ]
    bot_replies = [
        (1000.0, "   ，    DeepSeek"),
    ]

    prompt = build_prompt(message, recent_msgs, bot_replies)
    print(prompt)
    print()
    return True


def test_parse_json():
    """  JSON  （  AI）"""
    print("=" * 50)
    print("[  ] JSON ")
    print("=" * 50)

    from decider import _parse_json_output

    test_cases = [
        #   JSON
        '{"should_reply": true, "reason": "  ", "atmosphere": "  ", "atmosphere_confidence": 0.9, "strategy": "  ", "strategy_detail": "  "}',

        # ```json
        '```json\n{"should_reply": false, "reason": "  ", "atmosphere": "  ", "atmosphere_confidence": 0.8, "strategy": "  ", "strategy_detail": "  "}\n```',

        #     JSON
        '   ：{"should_reply": true, "reason": "  ", "atmosphere": "   ", "atmosphere_confidence": 0.95, "strategy": "   ", "strategy_detail": "    "}',

        #   JSON
        "       ",
    ]

    for i, tc in enumerate(test_cases, 1):
        result = _parse_json_output(tc)
        if result:
            print(f"  [{i}] OK : {json.dumps(result, ensure_ascii=False)[:60]}")
        else:
            print(f"  [{i}] FAIL (expected): {tc[:40]}")
    print()
    return True


def test_decision_dataclass():
    """  Decision  """
    print("=" * 50)
    print("[  ] Decision  ")
    print("=" * 50)

    d = Decision(
        should_reply=False,
        reason="  ，  Bot",
        atmosphere="  ",
        atmosphere_confidence=0.95,
        strategy="  ",
        strategy_detail="    ，  Bot  ",
    )

    print(f"  to_dict: {json.dumps(d.to_dict(), ensure_ascii=False)}")
    print(f"  to_prompt_injection: {d.to_prompt_injection()}")
    print()
    return True


def test_default_decision():
    """     """
    print("=" * 50)
    print("[  ]   ")
    print("=" * 50)

    d = Decision()
    print(f"   should_reply: {d.should_reply}")
    print(f"   atmosphere: {d.atmosphere}")
    print(f"   strategy: {d.strategy}")
    print()
    return True


def test_eggbloom_integration():
    """ EggBloom_v2       main.py   """
    print("=" * 50)
    print("[  ] EggBloom_v2   ")
    print("=" * 50)

    # 1. set_ai_client    （ slang_injector  ）
    print("  set_ai_client(ai) —    main.py   ai")
    print()

    # 2. to_prompt_injection()          system_prompt
    d = Decision(
        should_reply=True,
        reason="    ，    ",
        atmosphere="   ",
        atmosphere_confidence=0.85,
        strategy="   ",
        strategy_detail="   AI  ，      ",
    )
    injection = d.to_prompt_injection()
    print("  to_prompt_injection()  ：")
    print(f"  {repr(injection)}")
    print()
    print("   main.py call_deepseek      ：")
    print("  sys_prompt += decision.to_prompt_injection()")
    print()
    print("    system_prompt   ：")
    print("  [  ]   （  0.9）")
    print("  [  ]  ——   AI  ，      ")
    print()
    return True


def main():
    """     """
    print()
    print("=" * 50)
    print("  Group Chat Decider (EggBloom_v2 )")
    print("=" * 50)
    print()
    print(f"  : {ENABLED}")
    print()

    tests = [
        ("  prompt", test_build_prompt),
        ("  JSON", test_parse_json),
        ("Decision  ", test_decision_dataclass),
        ("  ", test_default_decision),
        ("EggBloom_v2  ", test_eggbloom_integration),
    ]

    passed = failed = 0
    for name, test_fn in tests:
        try:
            result = test_fn()
            if result:
                print(f"  [OK] {name} ")
                passed += 1
            else:
                print(f"  [FAIL] {name} ")
                failed += 1
        except Exception as e:
            print(f"  [FAIL] {name} : {e}")
            failed += 1

    print()
    print(f"  Result: {passed} passed, {failed} failed")
    print()

    #    API key
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("API_KEY")
    if api_key:
        print("   API Key，   AI  ...")
        try:
            from decider import _get_ai_client
            client = _get_ai_client()
            if client:
                result = decide(
                    message="   DeepSeek API   ？    ",
                    group_id="test_group",
                    context={
                        "recent_msgs": ["   ", "    "],
                        "bot_replies": [],
                    },
                )
                if result:
                    print("  ✓    :")
                    print(f"    should_reply: {result.should_reply}")
                    print(f"    reason: {result.reason}")
                    print(f"    atmosphere: {result.atmosphere} ({result.atmosphere_confidence:.1%})")
                    print(f"    strategy: {result.strategy} - {result.strategy_detail}")
                else:
                    print("  ✗    ")
            else:
                print("  - AI     ，   AI ")
        except Exception as e:
            print(f"  ✗   : {e}")
    else:
        print("  -  API Key   ，   AI ")
        print("    : set DEEPSEEK_API_KEY=your_key")
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())