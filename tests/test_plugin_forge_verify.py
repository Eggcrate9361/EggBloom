# -*- coding: utf-8 -*-
"""1.6.0 P3 · plugin_forge 接到 workflow 自验闭环。

钉死四件事：

  A. `self_verify()` 是一份五节点 DAG（syntax→manifest→abi→disk→report），
     运行态凭 run_id 可查，全程只做 AST / 字面量 / 字节比对，不执行插件代码
  B. 四类失败各自归因：语法、manifest、ABI 三原则、落盘字节不一致
  C. 提交时落盘自验不过就不留 staging、不写请求记录；通过则把 verify 凭据
     记进请求记录并出现在 status 里
  D. 审批与提交可能隔很久，`resolve(allow=True)` 移入 plugins/ 之前会再验一次；
     不过则代码留在 staging、状态 approved_verify_failed、正式目录不产生

全程用临时目录，不往真实 plugins/ 写任何东西。
运行：python tests/test_plugin_forge_verify.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import plugin_forge as pf  # noqa: E402
from harness import workflow as wf  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


GOOD_INIT = '''# -*- coding: utf-8 -*-
PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {"name": "p3probe", "version": "1.0.0",
                   "api_version": 2, "description": "self verify probe"}

def on_message(ctx=None, **kwargs):
    return None

PLUGIN_HOOKS = {"on_message": "on_message"}
'''
GOOD_FILES = {"__init__.py": GOOD_INIT}
ORIG = (pf._PLUGIN_DIR, pf._STAGING, pf._REQ_FILE, pf.self_verify)
ORIG_ENV = os.environ.get("EGGBLOOM_WORKFLOW_DIR")

with tempfile.TemporaryDirectory(prefix="eb-forge-") as tmp:
    base = Path(tmp)
    plugins_dir = base / "plugins"
    staging = plugins_dir / "_pending"
    plugins_dir.mkdir()
    pf._PLUGIN_DIR = str(plugins_dir)
    pf._STAGING = str(staging)
    pf._REQ_FILE = str(staging / "_requests.json")
    os.environ["EGGBLOOM_WORKFLOW_DIR"] = str(base / "workflows")

    print("\n[A] 五节点自验 DAG")
    v = pf.self_verify("p3probe", GOOD_FILES, run_id="p3-verify-ok")
    check("好插件通过", v.get("ok") is True, v)
    check("flow 名正确", v.get("flow") == pf.VERIFY_FLOW)
    check("五个节点都跑到 done",
          all(s == "done" for s in (v.get("step_status") or {}).values()),
          v.get("step_status"))
    check("运行态落盘可查", wf.status("p3-verify-ok") is not None)
    check("自验不 import 插件模块", "p3probe" not in sys.modules)
    check("report 节点给出 manifest 名", v.get("manifest_name") == "p3probe", v)

    print("\n[B] 四类失败归因")
    bad_syntax = {"__init__.py": GOOD_INIT + "\ndef (:\n"}
    check("语法错误 → failed=syntax",
          pf.self_verify("p3probe", bad_syntax, run_id="p3-v-syntax").get("failed") == "syntax")
    no_manifest = {"__init__.py": 'PLUGIN_SPEC = 1\nPLUGIN_HOOKS = {"on_message": "on_message"}\n'}
    check("缺 manifest → failed=manifest",
          pf.self_verify("p3probe", no_manifest, run_id="p3-v-manifest").get("failed") == "manifest")
    # 放进第二个 .py：_validate_files 只走 __init__.py 的 AST，
    # 而 _vf_abi 会逐个文件走 —— 这样才验到「自验层比提交校验多盖一层」。
    banned = {"__init__.py": GOOD_INIT,
              "helper.py": "import bot_core\n\ndef x():\n    return 1\n"}
    check("别的文件 import 主程序 → failed=abi",
          pf.self_verify("p3probe", banned, run_id="p3-v-abi").get("failed") == "abi")

    disk = base / "staged"
    disk.mkdir()
    (disk / "__init__.py").write_text(GOOD_INIT + "\n# tampered after submit\n",
                                      encoding="utf-8")
    check("落盘字节不一致 → failed=disk",
          pf.self_verify("p3probe", GOOD_FILES, staging_dir=str(disk),
                         run_id="p3-v-disk").get("failed") == "disk")
    clean = base / "staged_clean"
    clean.mkdir()
    (clean / "__init__.py").write_text(GOOD_INIT, encoding="utf-8")
    check("审批期只验盘也能通过",
          pf.self_verify("p3probe", staging_dir=str(clean),
                         run_id="p3-v-clean").get("ok") is True)

    print("\n[C] 提交时的自验闸")
    r = pf.plugin_forge(plugin_name="p3probe", description="自验探针",
                        files=GOOD_FILES)
    check("提交成功", r.get("ok") is True, r)
    check("staging 目录存在", (staging / "p3probe" / "__init__.py").is_file())
    recs = pf.history(10)
    check("请求记录带 verify 凭据",
          bool(recs) and (recs[0].get("verify") or {}).get("ok") is True, recs[:1])
    st = pf.plugin_forge_status()
    check("status 显示 verified", any(x.get("verified") for x in st["records"]), st)

    rid = recs[0]["request_id"]
    pf.self_verify = lambda *a, **kw: {"ok": False, "failed": "syntax",
                                      "problems": ["注入失败"], "run_id": "injected"}
    r2 = pf.plugin_forge(plugin_name="p3probe2", description="自验失败探针",
                         files={"__init__.py": GOOD_INIT.replace("p3probe", "p3probe2")})
    pf.self_verify = ORIG[3]
    check("自验不过 → 不提交", "落盘自验未通过" in str(r2.get("error")), r2)
    check("自验不过 → staging 清掉", not (staging / "p3probe2").exists())
    check("自验不过 → 不写请求记录",
          all(x.get("plugin") != "p3probe2" for x in pf.history(20)))

    print("\n[D] 审批期二次自验")
    shutil.rmtree(staging / "p3probe")
    (staging / "p3probe").mkdir()
    (staging / "p3probe" / "__init__.py").write_text(bad_syntax["__init__.py"],
                                                     encoding="utf-8")
    rr = pf.resolve(rid, True)
    check("自验不过 → approved_verify_failed",
          rr.get("status") == "approved_verify_failed", rr)
    check("代码留在 staging", (staging / "p3probe" / "__init__.py").is_file())
    check("正式目录不产生", not (plugins_dir / "p3probe").exists())
    check("记录里留 verify_run_id",
          any(x.get("status") == "approved_verify_failed" and x.get("verify_run_id")
              for x in pf.history(20)))

pf._PLUGIN_DIR, pf._STAGING, pf._REQ_FILE, pf.self_verify = ORIG
if ORIG_ENV is None:
    os.environ.pop("EGGBLOOM_WORKFLOW_DIR", None)
else:
    os.environ["EGGBLOOM_WORKFLOW_DIR"] = ORIG_ENV

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
