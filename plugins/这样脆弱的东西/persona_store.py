# -*- coding: utf-8 -*-
"""人格存储：为"高精度人格可能长到 1GB"设计的分层结构。

之前的问题：一个对象的所有东西塞在两个文件里（人格文件 .md + 证据 .json）。
300 条语料时没事；到了 1GB，**想看"他怎么称呼对方"就得把整个文件读进内存**，
注入卡也被迫跟着膨胀 —— 使用成本会压垮收益。

所以拆成四层，**每层独立寻址**：

  热层  card.md            ≤ 2KB，永远常驻（注入卡，直接喂模型）
  温层  persona.md         人格文件，人看的；按需加载
  冷层  sections/*.json    每个层一个文件，O(1) 定位，只读需要的那一层
  底层  evidence/shard-*   原文引证，按编号分片（每片 1000 个编号）

  加一份 manifest.json 当索引：各层的文件、字节数、条目数、证据分片区间。
  有了 manifest，"取他称呼对方的所有证据"就是：查 manifest → 读那一片 → 完事，
  跟总体积无关。

设计原则：**append-only + 原子写**。人格文件会被重印，但各层的 JSON
是账本状态的投影，重印不破坏历史。
"""
import json
import os
import re
import shutil
import time

from .config import get_config

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(PLUGIN_DIR, "data", "personas")

SHARD_SIZE = 1000          # 每个证据分片装多少个编号
SECTION_KEYS = ("identity", "linguistics", "behavior", "cognition",
                "relationship", "temporal", "environmental", "reconstruction",
                "sociology")


def _root(target_id):
    return os.path.join(STORE_DIR, str(target_id))


def _atomic_write(path, content):
    """原子写：先写临时文件再替换，避免写一半被读到。"""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    mode = "wb" if isinstance(content, bytes) else "w"
    enc = {} if isinstance(content, bytes) else {"encoding": "utf-8"}
    with open(tmp, mode, **enc) as f:
        f.write(content)
    os.replace(tmp, path)


def _write_json(path, obj):
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1))


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _mid_num(mid):
    m = re.match(r"M(\d+)", str(mid))
    return int(m.group(1)) if m else 0


def _shard_name(lo, hi):
    return f"evidence/e{lo:04d}-{hi:04d}.json"


def _shard_for(mid_num):
    lo = ((mid_num - 1) // SHARD_SIZE) * SHARD_SIZE + 1
    return lo, lo + SHARD_SIZE - 1


# ── 写入 ────────────────────────────────────────────────

def write(target_id, *, persona_md="", card_md="", evidence_md="",
          features=None, measures=None, style=None, relational=None,
          evidence_map=None, stats=None, ledger_view=None, level="", mode=""):
    """把一次蒸馏的产物写进分层存储，返回 manifest。"""
    tid = str(target_id)
    root = _root(tid)
    os.makedirs(root, exist_ok=True)

    # 1) 文本层
    if persona_md:
        _atomic_write(os.path.join(root, "persona.md"), persona_md)
    if card_md:
        _atomic_write(os.path.join(root, "card.md"), card_md)
    if evidence_md:
        _atomic_write(os.path.join(root, "evidence.md"), evidence_md)

    # 2) 各层 JSON（O(1) 定位）
    sections = {}
    for key, items in (features or {}).items():
        if not items:
            continue
        rel = f"sections/{key}.json"
        _write_json(os.path.join(root, rel), {"key": key, "entries": items})
        conf = [float(e.get("confidence", 0)) for e in items]
        sections[key] = {"file": rel, "entries": len(items),
                         "avg_conf": round(sum(conf) / len(conf), 3) if conf else 0}

    measures_files = {}
    for key, obj in (("psych", (measures or {}).get("psychology")),
                     ("sociolinguistics", (measures or {}).get("sociolinguistics")),
                     ("sociology", (measures or {}).get("sociology")),
                     ("style", style),
                     ("relational", relational)):
        if not obj:
            continue
        rel = f"measures/{key}.json"
        _write_json(os.path.join(root, rel), obj)
        measures_files[key] = {"file": rel}

    # 3) 证据分片（按编号区间，读哪段加载哪片）
    shards = {}
    for mid, rec in sorted((evidence_map or {}).items(),
                           key=lambda kv: _mid_num(kv[0])):
        num = _mid_num(mid)
        lo, hi = _shard_for(num)
        name = _shard_name(lo, hi)
        shards.setdefault(name, {})
        shards[name][mid] = rec
    shard_index = []
    for name, items in sorted(shards.items()):
        rel = name
        _write_json(os.path.join(root, rel), items)
        nums = [_mid_num(m) for m in items]
        shard_index.append({"file": rel, "count": len(items),
                            "lo": min(nums) if nums else 0,
                            "hi": max(nums) if nums else 0})

    # 4) manifest（索引）
    sizes = {}
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            sizes[rel] = os.path.getsize(fp)

    manifest = {
        "version": 3,
        "target_id": tid,
        "level": level,
        "mode": mode,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bytes_total": sum(sizes.values()),
        "files": sizes,
        "layers": {
            "card": {"file": "card.md", "bytes": sizes.get("card.md", 0)},
            "persona": {"file": "persona.md", "bytes": sizes.get("persona.md", 0)},
            "evidence": {"file": "evidence.md", "bytes": sizes.get("evidence.md", 0)},
        },
        "sections": sections,
        "measures": measures_files,
        "evidence_shards": shard_index,
        "stats": stats or {},
        "ledger_summary": {
            k: v for k, v in (ledger_view or {}).items()
            if k in ("run", "events", "at")
        },
    }
    _write_json(os.path.join(root, "manifest.json"), manifest)
    return manifest


def refresh_evidence(target_id, evidence_map):
    """只更新证据分片（蒸馏审核通过时引证会增补）。"""
    tid = str(target_id)
    root = _root(tid)
    if not os.path.isdir(root):
        return None
    manifest = _read_json(os.path.join(root, "manifest.json"), {}) or {}
    shards = {}
    for mid, rec in sorted((evidence_map or {}).items(),
                           key=lambda kv: _mid_num(kv[0])):
        lo, hi = _shard_for(_mid_num(mid))
        name = _shard_name(lo, hi)
        shards.setdefault(name, {})
        cur = shards[name]
        cur.update({mid: rec})
        merged = dict(_read_json(os.path.join(root, name), {}) or {})
        merged.update(cur)
        shards[name] = merged
    shard_index = []
    for name, items in sorted(shards.items()):
        _write_json(os.path.join(root, name), items)
        nums = [_mid_num(m) for m in items]
        shard_index.append({"file": name, "count": len(items),
                            "lo": min(nums) if nums else 0,
                            "hi": max(nums) if nums else 0})
    manifest["evidence_shards"] = shard_index
    _write_json(os.path.join(root, "manifest.json"), manifest)
    return manifest


# ── 读取 ────────────────────────────────────────────────

def manifest(target_id):
    return _read_json(os.path.join(_root(str(target_id)), "manifest.json"), {}) or {}


def exists(target_id):
    return os.path.isdir(_root(str(target_id)))


def load_layer(target_id, layer):
    """读一层：card / persona / evidence / manifest。"""
    root = _root(str(target_id))
    if layer == "manifest":
        return manifest(target_id)
    p = os.path.join(root, f"{layer}.md")
    if not os.path.exists(p):
        return ""
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def load_section(target_id, key):
    """读某一层（O(1)：查 manifest → 读那一个文件）。"""
    m = manifest(str(target_id))
    info = (m.get("sections") or {}).get(key)
    if not info:
        return []
    return (_read_json(os.path.join(_root(str(target_id)), info["file"]), {})
            or {}).get("entries") or []


def load_measure(target_id, key):
    m = manifest(str(target_id))
    info = (m.get("measures") or {}).get(key)
    if not info:
        return {}
    return _read_json(os.path.join(_root(str(target_id)), info["file"]), {}) or {}


def load_evidence(target_id, mids=None):
    """读原文引证。给了 mids 就只加载需要的分片 —— 1GB 规模下这是关键。"""
    root = _root(str(target_id))
    m = manifest(target_id)
    shards = m.get("evidence_shards") or []
    if mids is None:
        wanted = [s["file"] for s in shards]
    else:
        need = set()
        for mid in mids:
            lo, _hi = _shard_for(_mid_num(mid))
            for s in shards:
                if s["lo"] <= _mid_num(mid) <= s["hi"]:
                    need.add(s["file"])
                    break
        wanted = sorted(need)
    out = {}
    for rel in wanted:
        data = _read_json(os.path.join(root, rel), {}) or {}
        out.update(data)
    if mids is not None:
        out = {k: v for k, v in out.items() if k in set(mids)}
    return out


def stats(target_id):
    """体积概览 —— 让"1GB 人格"的使用成本可见。"""
    m = manifest(target_id)
    secs = m.get("sections") or {}
    return {
        "bytes_total": m.get("bytes_total", 0),
        "layers": {k: v.get("bytes") for k, v in (m.get("layers") or {}).items()},
        "sections": {k: v.get("entries") for k, v in secs.items()},
        "evidence_shards": len(m.get("evidence_shards") or []),
        "updated_at": m.get("updated_at"),
    }
