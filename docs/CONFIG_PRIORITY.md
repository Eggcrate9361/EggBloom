# EggBloom 配置加载优先级

> 本文档描述 `config.py` 的完整配置回退链。修改配置相关代码时请参考此图。

## 配置值加载优先级（高 → 低）

```
┌─────────────────────────────────────────────────────────────────┐
│  1. Windows 凭据管理器 (cred_store.py)                           │
│     仅存 API Key，不落盘明文                                      │
│     读取: read_cred(MODEL_API_KEY_CRED)                         │
├─────────────────────────────────────────────────────────────────┤
│  2. 程序目录 bot_config.json (APP_CONFIG_FILE)                   │
│     路径: {SCRIPT_DIR}/bot_config.json                          │
│     行为: 直接 update 到 DEFAULT_CONFIG 上                       │
├─────────────────────────────────────────────────────────────────┤
│  3. 数据目录 bot_config.json (CONFIG_FILE)                       │
│     路径: {数据目录}/bot_config.json                              │
│     行为: 仅补缺（字段不存在或为空时才写入）                        │
├─────────────────────────────────────────────────────────────────┤
│  4. .env 文件 (ENV_FILE)                                        │
│     路径: {数据目录}/.env                                        │
│     映射: API_KEY→api_key, API_URL→api_url, MODEL_NAME→model_name│
├─────────────────────────────────────────────────────────────────┤
│  5. DEFAULT_CONFIG (config.py 内置)                              │
│     所有配置项的默认值                                            │
└─────────────────────────────────────────────────────────────────┘
```

## 数据目录解析优先级（data_store.py）

```
1. Electron 环境变量 EGG_BLOOM_USER_DATA（打包模式）
2. 开发模式：项目根目录下的 data/（源码运行）
3. 平台标准目录：
   - Windows: %LOCALAPPDATA%/EggBloom
   - macOS: ~/Library/Application Support/EggBloom
   - Linux: ~/.local/share/EggBloom
```

## 配置写入优先级（save_config）

```
1. 程序目录 {SCRIPT_DIR}/bot_config.json（优先）
2. 数据目录 {数据目录}/bot_config.json（程序目录不可写时回退）
```

## API Key 读取优先级（get_api_key）

```
1. Windows 凭据管理器 (read_cred)
2. 配置中的 api_key 字段
3. 环境变量 API_KEY
4. 环境变量 DEEPSEEK_API_KEY
```

## 关键文件

| 文件 | 职责 |
|------|------|
| `config.py` | 配置加载/保存/迁移/完整性检查 |
| `data_store.py` | 数据目录解析（Electron/开发/平台三模式） |
| `cred_store.py` | Windows 凭据管理器读写 |
| `bot_config.json.example` | 配置模板（新增配置项必须同步） |
| `.env.example` | 环境变量模板 |

## 注意事项

- 新增配置项必须同时更新 `DEFAULT_CONFIG`、`bot_config.json.example`、`.env.example`
- API Key 永远不写入 JSON 文件（`save_config` 默认排除 `api_key`）
- 配置有弱缓存（文件 mtime+size 未变则复用），保存后自动失效
- 旧字段 `deepseek_api_key` 自动迁移为 `api_key`
