import os
import json
import base64
import requests
import time

# 配置
MEME_DIR = os.path.dirname(os.path.abspath(__file__))
TAGS_FILE = os.path.join(MEME_DIR, "tags.json")
API_URL = "http://127.0.0.1:11434/api/generate"  # Ollama 本地 API

# 标签候选集（基于绪山真寻常见表情）
CORE_TAGS = [
    "坏笑", "害羞", "震惊", "摆烂", "无语", "尴尬", "慌张", "开心", "得意", "委屈",
    "生气", "害怕", "疑惑", "期待", "失望", "惊喜", "困", "累", "加油", "可爱",
    "傲娇", "嫌弃", "无奈", "崩溃", "兴奋", "呆", "哭", "笑", "捂脸", "指",
    "比耶", "点赞", "拒绝", "同意", "思考", "睡觉", "吃饭", "工作", "学习", "游戏"
]

def encode_image_to_base64(image_path):
    """将图片转为 base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

def call_ollama_vision(image_path, prompt):
    """调用本地 Ollama 视觉模型（需要安装并运行 Ollama + qwen2.5-vision）"""
    base64_image = encode_image_to_base64(image_path)
    
    payload = {
        "model": "qwen2.5-vision:7b",  # 或 llama3.2-vision:11b
        "prompt": prompt,
        "stream": False,
        "images": [base64_image]
    }
    
    try:
        response = requests.post(API_URL, json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            return result.get("response", "").strip()
        else:
            print(f"  API error: {response.status_code}")
            return None
    except Exception as e:
        print(f"  Request error: {e}")
        return None

def extract_tags_from_description(desc):
    """从 AI 描述中提取标签"""
    tags = set()
    desc_lower = desc.lower()
    
    # 映射关键词 -> 标签
    keyword_map = {
        "坏笑": ["坏笑", "奸笑", "邪恶", "狡猾", "得意"],
        "害羞": ["害羞", "脸红", "不好意思", "腼腆"],
        "震惊": ["震惊", "惊讶", "吓到", "目瞪口呆"],
        "摆烂": ["摆烂", "放弃", "躺平", "无所谓"],
        "无语": ["无语", "无奈", "汗颜", "扶额"],
        "尴尬": ["尴尬", "窘迫", "不知所措"],
        "慌张": ["慌张", "慌乱", "手忙脚乱"],
        "开心": ["开心", "高兴", "快乐", "笑"],
        "得意": ["得意", "骄傲", "自豪"],
        "委屈": ["委屈", "难过", "伤心", "哭"],
        "生气": ["生气", "愤怒", "发火"],
        "害怕": ["害怕", "恐惧", "吓"],
        "疑惑": ["疑惑", "疑问", "问号", "？"],
        "期待": ["期待", "盼望", "等待"],
        "失望": ["失望", "失落", "沮丧"],
        "惊喜": ["惊喜", "惊喜", "哇"],
        "困": ["困", "睡觉", "睡"],
        "累": ["累", "疲惫", "疲倦"],
        "加油": ["加油", "努力", "奋斗"],
        "可爱": ["可爱", "萌", "萌萌哒"]
    }
    
    for tag, keywords in keyword_map.items():
        if any(kw in desc for kw in keywords):
            tags.add(tag)
    
    # 如果没匹配到，返回前 3 个核心标签作为兜底
    if not tags:
        tags = set(CORE_TAGS[:3])
    
    return list(tags)

def tag_single_meme(image_path, existing_tags=None):
    """给单张表情包打标签"""
    if existing_tags:
        return existing_tags  # 已有标签则跳过
    
    prompt = """请用一句话描述这张图片中人物的表情、动作和情绪。尽量简洁，用中文。
例如：一个女孩在坏笑，看起来很得意。
例如：一个害羞的女孩捂着脸，很尴尬。
直接描述，不要加前缀。"""
    
    print(f"分析: {os.path.basename(image_path)}")
    desc = call_ollama_vision(image_path, prompt)
    if not desc:
        print("  -> 分析失败，使用默认标签")
        return CORE_TAGS[:3]
    
    print(f"  -> AI 描述: {desc}")
    tags = extract_tags_from_description(desc)
    print(f"  -> 提取标签: {tags}")
    return tags

def main():
    # 检查 Ollama 是否运行
    try:
        resp = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
        if resp.status_code != 200:
            print("❌ Ollama 未运行。请先安装并启动 Ollama，然后运行:")
            print("   ollama pull qwen2.5-vision:7b")
            print("   ollama run qwen2.5-vision:7b")
            return
    except:
        print("❌ 无法连接到 Ollama。请确保 Ollama 在 localhost:11434 运行。")
        return
    
    # 加载已有标签
    if os.path.exists(TAGS_FILE):
        with open(TAGS_FILE, 'r', encoding='utf-8') as f:
            tag_db = json.load(f)
    else:
        tag_db = {}
    
    # 获取所有图片文件
    image_exts = ('.png', '.jpg', '.jpeg', '.gif')
    image_files = [f for f in os.listdir(MEME_DIR) if f.lower().endswith(image_exts)]
    total = len(image_files)
    
    print(f"开始处理 {total} 张表情包...")
    
    processed = 0
    for idx, filename in enumerate(image_files):
        if filename in tag_db:
            processed += 1
            continue
            
        image_path = os.path.join(MEME_DIR, filename)
        tags = tag_single_meme(image_path)
        
        tag_db[filename] = {
            "tags": tags,
            "path": image_path,
            "size": os.path.getsize(image_path)
        }
        
        processed += 1
        print(f"进度: {processed}/{total}")
        
        # 每 5 张保存一次
        if processed % 5 == 0:
            with open(TAGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(tag_db, f, ensure_ascii=False, indent=2)
            print("  已保存中间结果")
        
        time.sleep(1)  # 避免 API 过载
    
    # 最终保存
    with open(TAGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(tag_db, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 完成！已保存标签到 {TAGS_FILE}")
    print(f"   共处理 {len(tag_db)} 张表情包")

def quick_tag_by_filename():
    """快速根据文件名打标签（如果不想跑 AI）"""
    tag_db = {}
    for filename in os.listdir(MEME_DIR):
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            continue
        
        # 根据文件名猜测标签
        f_lower = filename.lower()
        tags = []
        
        if 'bad' in f_lower or 'evil' in f_lower:
            tags.append("坏笑")
        if 'shy' in f_lower or 'blush' in f_lower:
            tags.append("害羞")
        if 'shock' in f_lower or 'surprise' in f_lower:
            tags.append("震惊")
        if 'lazy' in f_lower or 'giveup' in f_lower:
            tags.append("摆烂")
        if 'happy' in f_lower or 'smile' in f_lower:
            tags.append("开心")
        if 'cry' in f_lower or 'sad' in f_lower:
            tags.append("委屈")
        if 'angry' in f_lower:
            tags.append("生气")
        if 'scared' in f_lower:
            tags.append("害怕")
        
        if not tags:
            tags = ["可爱", "二次元"]  # 默认
        
        tag_db[filename] = {
            "tags": tags,
            "path": os.path.join(MEME_DIR, filename),
            "size": os.path.getsize(os.path.join(MEME_DIR, filename))
        }
    
    with open(TAGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(tag_db, f, ensure_ascii=False, indent=2)
    
    print(f"快速标签完成！共 {len(tag_db)} 张，保存到 {TAGS_FILE}")

if __name__ == "__main__":
    # 选择模式
    print("选择标签模式:")
    print("1. 使用 Ollama AI 分析（需要安装 Ollama + qwen2.5-vision）")
    print("2. 快速根据文件名猜测标签")
    choice = input("输入 1 或 2: ").strip()
    
    if choice == "1":
        main()
    else:
        quick_tag_by_filename()