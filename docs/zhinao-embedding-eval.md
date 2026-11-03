# Zhinao-ChineseModernBert-Embedding 升级评估

> 评估对象：`qihoo360/Zhinao-ChineseModernBert-Embedding`
> 评估时间：2026-11-03　环境：本机 CPU，llama.cpp b10068，同一 `/v1/embeddings` 接口
> 结论：**不建议换。** 在本项目真实任务形态（中文群聊短句 ↔ 记忆短串）上，它比现在的 `bge-small-zh-v1.5` 更差，且体积、内存、延迟全面更贵。

---

## 一、先纠正三处事实

| 项 | 流传的说法 | 实际（已核对 config.json / 1_Pooling/config.json） |
|---|---|---|
| 仓库名 | `360zhinao/Zhinao-ChineseModernBert-Embedding` | **`qihoo360/Zhinao-ChineseModernBert-Embedding`**（`360zhinao` 是组织别名，HF 上搜不到；CMTEB 榜单也记作 qihoo360） |
| 维度 | 常被当成 1024（bge-base 同档） | **768**；CLS pooling + L2 归一化 |
| 参数量 | "Base 级 ~100M" | **227M**（其中 embedding 表 151936×768 ≈ 116.7M，占一半以上） |

其余关键参数：

- 架构 ModernBERT，22 层，12 头，FFN 1152，`global_attn_every_n_layers=3`，`local_attention=128`
- 分词器是 **Qwen2 BPE**（vocab 151936，CLS=151644 / SEP=151666），不是 BERT WordPiece
- 最大序列 **512** token（基座是 1536，嵌入模型被切到 512）
- 许可证 **Apache-2.0**（可商用、可随包分发）
- 官方用法要求查询侧加指令前缀：`Instruct: Retrieve semantically similar text.\nQuery: `

CMTEB 官方数字：Mean **70.16**（bge-base-zh-v1.5 63.07，Qwen3-Embedding-0.6B 67.45），Dimension 768。

---

## 二、实测：跑了三组，方向一致

### 2.1 方法

- 两个模型各自转成 GGUF f16，用**同一个** llama-server 二进制、同样的 `--embeddings`，只换 `-m`
- 先做**数值对齐校验**：同一句话分别用 transformers 原生实现和 llama-server 出向量，余弦 **1.00000**（4 句全对）→ 转换与推理链路无问题，后面测的就是模型本身
- 分词一致性：`我晚上总是翻来覆去到天亮` → llama-server `[35946,104030,...]`，HF `[151644, 35946,...,151645]`，**逐 id 一致**
- 按生产口径嵌入：只嵌正文，**不带** `[类别] ` 前缀（代码里 `_upsert_fact` 嵌的就是 `content`）

### 2.2 主测试：30 条查询 × 40 条候选记忆

查询是群聊口语（"我那个项目又崩了"、"我是学什么的来着"），候选是 facts/events 那种短串，每条查询有唯一 gold。

| 指标 | bge-small-zh-v1.5 | Zhinao-CMB-Embedding | 差 |
|---|---|---|---|
| 维度 | 512 | 768 | |
| Top-1 命中 | **0.833** | 0.767 | −6.6 pt |
| Top-3 命中 | **0.933** | 0.833 | −10.0 pt |
| MRR | **0.885** | 0.814 | −0.071 |
| gold 相似度均值 | 0.621 | 0.487 | |
| 最强干扰项均值 | 0.505 | 0.371 | |
| 平均 margin | 0.116 | 0.116 | 打平 |

30 条里有 10 条排序不同，**bge 赢 7 条、Zhinao 赢 3 条**。分歧的形态很有规律：Zhinao 赢的都是"本来就该排第一、它把第 2/3 提到第 1"；bge 赢的是"它把第 1 掉到第 3~13"。

| 查询 → gold | bge | Zhinao |
|---|---|---|
| 我最喜欢哪个游戏 → 看明日方舟的立绘 | **1** | 13 |
| 我念高几了 → 高三学生 | **1** | 9 |
| 我那个项目又崩了 → 项目构建失败，重装了依赖 | **1** | 4 |
| 我现在人在哪 → 在图书馆自习 | **9** | 11 |
| 我昨晚吃了啥 → 昨天和同学去吃了火锅 | 2 | **1** |
| 我昨天运动了吗 → 在操场跑了三公里 | 3 | **1** |
| 你记不记得我养的那只猫 → 养了一只橘猫叫团子 | 2 | **1** |

也就是说：Zhinao 赢的 3 条，bge 本来就在第 2~3（差距很小）；输的 7 条里，有 4 条是"bge 第 1、它掉到第 3~13"。对"只注入 Top-3 条记忆"的用法，掉到第 9/13 是直接丢记忆，这就是 Top-3 掉 10 个点的来源。

### 2.3 纯语义组：10 对零字面重叠

专门挑语义模型该赢的场景（"我晚上总是翻来覆去到天亮" ↔ "凌晨五点还没睡，失眠"）。

| | bge | Zhinao |
|---|---|---|
| 平均相似度 | **0.593** | 0.445 |
| 最低 | **0.449** | 0.190 |

### 2.4 长文档组：8 对，候选扩到 60~70 字

模拟"用户问一句 → 召回一条几百字的历史消息"。

| | bge | Zhinao |
|---|---|---|
| 平均相似度 | **0.634** | 0.479 |
| 最低 | **0.514** | 0.176 |

### 2.5 分布与指令

| 项 | bge | Zhinao | 解读 |
|---|---|---|---|
| 无关对相似度均值 | 0.278 | 0.052 | Zhinao 的底噪更低 |
| 无关对最高 | 0.311 | 0.120 | 但见下 |
| 否定对（我喜欢/我不喜欢香菜） | 0.827 | 0.748 | **两家都分不开否定**，别指望靠向量解决 |
| 加官方查询指令后（纯语义组） | 0.530 | 0.325 | **两家都变差**，别加 |

阈值标定（宁可多召回一点）：

| 阈值 | bge 保住真匹配 / 放进干扰项 | Zhinao 保住真匹配 / 放进干扰项 |
|---|---|---|
| 0.20 | 1.000 / 0.964 | 0.933 / 0.228 |
| 0.30 | 1.000 / 0.550 | 0.833 / 0.054 |
| **0.35（现值）** | **1.000 / 0.290** | 0.800 / 0.024 |
| 0.50 | 0.900 / 0.024 | 0.500 / 0.003 |

现网 `embed_min_sim=0.35` 对 bge 是**标定得刚好**的（真匹配 100% 保留）。换成 Zhinao 后沿用 0.35 会砍掉 20% 的真匹配；要保到 100% 得降到 0.15 左右，那时 40% 的干扰项都会进来。

### 2.6 成本

| | bge-small-zh | Zhinao | 倍数 |
|---|---|---|---|
| GGUF f16 | 45.7 MB | 454 MB | **10×** |
| 常驻内存（llama-server RSS） | 516 MB | 852 MB | +336 MB |
| 短句延迟（CPU） | 5.9 ms | 17.7 ms | **3×** |
| 800 字延迟 | 22.2 ms | 89.6 ms | **4×** |

对一个还要同时跑 LLM 的桌面端，+336 MB 常驻 + 分发体积 10 倍，不是小数目。

### 2.7 引擎交叉验证：LiGGUF

项目实际首选的是进程内 `ligguf_embed.dll`（`_init_dll()` 成功就走它，llama-server 只是兜底）。
所以又在**真实 DLL** 上用同一套 30×40 重跑了一遍 bge —— 用的就是部署目录
`%LOCALAPPDATA%\EggBloom_v3\models\` 里那份（298,496 B）和同一个 `bge-small-zh-v1.5-f16.gguf`：

| 引擎 | Top-1 | Top-3 | MRR | 单条延迟 |
|---|---|---|---|---|
| LiGGUF（插件首选） | 0.833 | 0.900 | 0.878 | **67 ms** |
| llama-server（兜底） | 0.833 | 0.933 | 0.885 | 5.9 ms |

Top-1 完全一致，Top-3 差 3 个点（引擎数值路径不同，同文余弦 0.891，见 LiGGUF-CN 的基准报告）。
**结论不受影响**；但两点要记住：

- LiGGUF 单条 **67 ms**，是 llama-server 的 11 倍 —— 这条路径上任何"多嵌一次"都是 70 ms 级的浪费；
- 两个引擎的向量不能混用（同一句话余弦 0.891 而非 1.0）。换引擎等于换口语义空间，必须整库重算。

---

## 三、为什么榜上 70.16 的输给了 63.07

三个原因，按重要性排：

1. **CMTEB 分数是 in-domain 的。** 模型卡原文：「基于 CMTEB 与 MTEB **全维度任务相关数据集**，完成多任务有监督微调」。训练数据就是评测集的任务数据，这个 70.16 只能说明它把这些任务学得好，不能说明它迁移到别的分布还好。我们的任务（10 字口语短句 ↔ 15 字记忆串）不在那个分布里。
2. **任务形态不匹配。** CMTEB 的 Retrieval 是"短 query 检长 passage"，模型有上下文可推。这里 query 和 doc 都只有十几个字，几乎没有可供语义推断的冗余，能依赖的基本是字面与近义 —— 这正是 `bge-small-zh-v1.5` 的训练数据（中文检索、STS 短句对）覆盖的形态。
3. **相似度分布整体下移，真正决定排序的 margin 却没变。** 无关对从 0.28 掉到 0.05，真匹配从 0.62 掉到 0.49，**平均 margin 两边都是 0.116**。也就是说它并没有"分得更开"，只是把尺子换了刻度 —— 换模型就等于必须重标定所有阈值，而精度并不会因此变好。

> 注：本次测的是短文本记忆召回，**不能**外推成"Zhinao 不如 bge"。拿它做长文档 RAG、分类、聚类，按 CMTEB 的分布它大概率仍然更强。结论只在 EggBloom 这个用法上成立。

---

## 四、如果还是要换，代码上必须改的地方

现有实现里，换模型不是改一个配置项的事：

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `plugins/ego/super_memory/__init__.py:129-134` | `ctypes.c_float * 512`、`maxdim=512` 写死 | 768 维模型时 DLL 返回错误码 5，累计 5 次后 `_available()` 永久 False，**整条向量召回静默失效** |
| 2 | `:219-233` `_vec_sim` | 长度不等直接 `return 0.0` | 新旧向量混存时**全部算 0 分**，再被阈值筛掉 → **存量记忆静默消失**（facts/events 的 embedding 列没有模型标识，无法区分是哪版模型算的） |
| 3 | `:939` `embed_min_sim` + `harness/memory_context.py:26` `tau=0.35` | 按 bge 的分布标定 | Zhinao 下真匹配保留率跌到 80% |
| 4 | `:745-755`、`:901-905` | `if _embedder.embed(x): emb = json.dumps(_embedder.embed(x))` **每条嵌两次** | bge 下浪费 6ms 无所谓，Zhinao 下是白烧 18ms/条 |
| 5 | `webui/webui_server.py:486-491` | 拉起参数 `-c 8192`，无 `--pooling` | 依赖 GGUF 里的 `pooling_type`；Zhinao 转出来是 CLS(2)，bge 也是 CLS，这条恰好不用改，但换别的模型要核对 |
| 6 | `plugins/ego/super_memory/__init__.py:46-48` | 默认 `embed_exe` 指向 `<llama.cpp 目录>\...`、DLL 路径指向 `models/ligguf_embed.dll` | 见下 |

### 关于 LiGGUF DLL 那条路径：**它跑不了这个模型，改不了**

`<LiGGUF 源码目录>\vdb.cpp` 是手写的 BERT 专用引擎：

- `bert_tokenize()` 是 WordPiece + `▁` 前缀的最长贪心匹配，**不认识 Qwen2 BPE**；vocab 151936 会整句变 `[UNK]`
- `vdb_embed_export()` 里 `if (maxdim < n) return 5;` —— 768 > 512 直接报错
- pooling 是 mean，Zhinao 要 CLS

也就是说**要跑 Zhinao，这条 DLL 路径必须弃用**，只能走 llama-server HTTP（本次全部测试即走 HTTP，Zhinao 侧数据有效）。
但**跑 bge 时 DLL 是正常的**，实测 0.833/0.900，与 llama-server 同级 —— 不是"这条路径有问题"，是"这条路径只认 BERT 口径"。

这条代价要单独算：换 Zhinao = **放弃进程内 LiGGUF、永久常驻一个 llama-server 进程**。
包体 +408 MB（45.7 → 454 MB）、常驻 +336 MB（516 → 852 MB），换来的却是召回变差。
唯一"变好"的是延迟（Llama-server 18 ms vs LiGGUF bge 67 ms），但那是换引擎带来的，跟换模型无关 ——
想要更快，直接把 bge 挪到 llama-server 就行，不必换模型。

另：转换本身也有坑 —— 360 的 `tokenizer.json` 哈希不在 llama.cpp 的 BPE 预分词器白名单里，直接 `convert_hf_to_gguf.py` 会 `NotImplementedError`。需要在 `conversion/base.py` 的 `get_vocab_base_pre()` 里补一行：

```python
if chkhsh == "8c92fb84193ca7b427b59c124363b8475bc5a611a011e39b1dba7898cd34cc7d":
    # ref: https://huggingface.co/qihoo360/Zhinao-ChineseModernBert-Embedding
    res = "qwen2"
```

补完即可得到 `general.architecture = modern-bert`，llama.cpp b10068 能直接加载（`llama.dll` 里有该架构）。公开的 `VelociQ8/...-Q8A64-GGUF` **不能用**：它是私有打包格式（`general.architecture = a64b9e07`，只有 2 个张量），必须配它自己的 runtime。

---

## 五、真正的瓶颈不在模型

翻完代码，向量这条路现在的问题不是"模型不够好"：

1. **覆盖率为零的部分最大。** `messages` 表（618 条，其中大部分是 `_test_*` / `_t_*` 测试作用域）**根本没有 embedding 列**，`after_reply` 只写文本。向量只用在 `facts`(7 条) 和 `events`(1 条) 上，而且这 8 条里也只有 3+1 条真的算过向量。
2. **主召回是纯词法的。** `sandglass_lite.search_scored()` 走 `query_tokens` + token 重叠打分，一条向量都没用。群聊里"换个说法"的召回全靠它，这才是"记不住"的主因。
3. **换模型对上述两点毫无帮助。**

在 8 条向量上换一个贵 10 倍的模型，收益是 0；把 618 条对话向量化 + 和词法做混合排序，收益是可测的。

---

## 六、建议

**不做**：换 Zhinao。

**做**（按性价比）：

| 优先级 | 动作 | 预期收益 | 成本 |
|---|---|---|---|
| P0 | `_upsert_fact` 嵌 `f"[{category}] {content}"` 而不是裸 `content` | 同集 Top-1 0.833 → 0.867、Top-3 0.933 → 0.967（只多命中 1 条，样本小，属弱证据，但成本是改 1 行） | 改 1 行 |
| P0 | 给 `messages` 补 `embedding` 列 + 写入时算向量，召回并入 `memory_context.collect` | 从"没有语义召回"到"有" | 中等 |
| P1 | 向量分与沙漏词法分做 RRF 融合（`harness/memory_context.py` 已有池子+去重骨架，加一路融合即可） | 短中文场景通常 +5~10 pt 召回 | 中等 |
| P1 | 去掉 `_upsert_fact` / `_store_events` 的双次 embed | 每条省一半 embedding 开销 | 改 2 处 |
| P2 | 若要换模型，先用 `<评测脚本目录>\bench3.py` 在自己的数据上过一遍，门槛是"Top-1 > 0.833" | 避免再花一次冤枉钱 | 低 |

候选顺序（**未实测**，仅按参数/生态排序）：`bge-large-zh-v1.5`（1024 维，同族同分词器，迁移成本最低）→ `gte-large-zh` → `Qwen3-Embedding-0.6B`（1024 维，多语言强但 2.6 倍参数）。**任何一个都必须先在 bench3 上过线再谈接入。**

> 试过 `CompendiumLabs/bge-large-zh-v1.5-gguf`：**加载不了**（`duplicate key 'tokenizer.ggml.bos_token_id'`，旧版转换器产物）。要用得自己从 HF safetensors 转。

---

## 七、复现

```powershell
# 1) 转换（需在 llama.cpp 源码目录，先打上面那行 pre-tokenizer 补丁）
python llama.cpp-master\convert_hf_to_gguf.py zh_model --outfile zhinao-f16.gguf --outtype f16

# 2) 起服务
<llama.cpp 目录>\llama-server.exe -m zhinao-f16.gguf --embeddings -c 512 --port 8084 --host 127.0.0.1

# 3) 跑分（任意 OpenAI 兼容端点都能测）
python <评测脚本目录>\bench3.py http://127.0.0.1:8084/v1/embeddings zhinao
python <评测脚本目录>\bench3.py http://127.0.0.1:8085/v1/embeddings bge
```

产物：`zhinao-f16.gguf`（454 MB）、`bge.gguf`（45.7 MB）、评测脚本与原始结果 JSON 均在 `<评测脚本目录>\`。
