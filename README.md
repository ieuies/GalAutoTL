# GalAutoTL — Galgame 自动汉化工具

图形界面：选择游戏目录 → 填写 DeepSeek / OpenAI 兼容 API → 自动探测引擎并批量 AI 翻译。

## 快速开始（一键）

1. 双击 `运行.bat`（会自动装依赖，含 UnityPy）  
2. 在窗口里 **只选游戏根目录**  
3. 填 API Key → 点 **开始汉化**  

工具会自动探测引擎并翻译写回，一般不用再选管线、不用手动 `pip install`。

开发调试也可用：

```bat
py -3 -m app.main
```

### 自动化测试（回归）

改核心逻辑后建议跑一遍，防止旧坑复辟：

```bat
运行测试.bat
```

或：

```bat
py -3 -m pip install pytest
py -3 -m pytest tests -q
```

覆盖说明见 `tests/验收清单.txt`（图片 UI / 译质 / 实机等）。

用例分三层：行为回归、假 AI 质量门禁、迷你 e2e。

## 引擎能力卡

| 引擎 | 管线 | 一键程度 | 编码 | 二次全量 | 仅译漏句 | 主要限制 |
|------|------|----------|------|----------|----------|----------|
| Kirikiri / XP3 | `kirikiri` | 高 | UTF-16 | 原包/备份+缓存 | ✓ | cxdec 需 garbro / unencrypted |
| SoftPal | `softpal` | 高 | zh→GBK | PAC/备份+缓存 | ✓ | 非经典 SoftPal 另论 |
| Kagura | `kagura` | 高 | CP932 | 备份 pak | ✓ | 槽位截断 / `・` |
| Artemis | `artemis` | 高 | UTF-8 | PFS 日文源 | ✓ | `.asb` 弱 |
| LCSE / Liquid | `lcse` | 高 | GBK | 备份封包 | ✓ | 用中文 bat，勿日语 LE |
| BGI / Ethornell | `bgi` | 中高 | CP932 | 备份 arc | ✓ | 可能需回封 arc |
| YU-RIS | `yuris` | 中高 | GBK 写 | 备份 YPF | ✓ | 显示乱码需引擎补丁 |
| SakanaGL | `sakana` | 中高 | UTF-8 | 备份 sx | ✓ | 过长句塞不回槽 |
| Unity | `unity` | 中高 | UTF-8 词典 | 词典合并 | ✓ | 依赖 BepInEx/XUA |
| RealLive | `reallive` | 中 | UTF-8 导出 | 跳过已有场景+补翻 | ✓ | 需 export_utf8 / 外置工具 |
| 通用文本 | `generic` | 中 | 可调 | 视目录 | ✓ | 先解包明文 |

**按钮怎么用：** 首次用「开始汉化」→ 漏句看 `GalAutoTL_remain.txt` →「仅译漏句」；手改用 `GalAutoTL_review.txt`。不要清 `%APPDATA%\GalAutoTL\cache.sqlite` 后再全量，除非刻意重译。

**译质（人设 / 术语）：**  
- 默认人设规则内置；首次汉化会在游戏目录生成可改的 `GalAutoTL_persona.txt`（改它即覆盖默认，删掉则恢复内置）。  
- 角色专属仍写在 `GalAutoTL_glossary.txt`：`あや=绫 ;; 女性，自称「我」，对主角用「你」，叙述用「她」`。  
- 备注只进提示词，不写进台词；引擎仍按译名硬替换。

打包 EXE（已打入 UnityPy）：

```bat
build_exe.bat
```

完成后在 `dist\GalAutoTL.exe`。

## 使用说明

### 普通游戏（能直接在中文 Windows 打开）— 推荐简单模式

1. 勾选 **简单模式**（默认开启）
2. 用 GARbro 等工具**先解包**，得到含 `txt` / `json` / `ks` / `po` 的文件夹
3. 选择该 **文本文件夹**
4. **源语言**选日文 / 英文 / 韩文 / 俄文 / 自动 / 其它
5. 填 API Key → **开始汉化**

### Liquid / LCSE（`lcsebody1`，如《大催眠乱交学院》）— 一键可用

实测流程已固化进工具：

1. **游戏根目录**选到含 `lcsebody1` + `lcsebody1.lst` 的文件夹  
2. 点 **探测引擎**（切到 LCSE）或手动选 **LCSE / Liquid（一键回封）**  
3. 源语言 **日文**，填 API Key → **开始汉化**

自动完成：

| 步骤 | 作用 |
|------|------|
| 备份 | 桌面 `自动翻译备份\lcse_游戏名\`（封包+exe，首次原版会保留） |
| 解包 | 从**备份原版**解开 SNX |
| AI 精翻 | 对白 / 选项 / 角色名 |
| 槽位硬化 | 每条译文夹紧到**原版字节长度**，指令表不动；保留 `\x02\x03` 结束符（防中途点不动） |
| 回封 | 替换进**原版封包**再写回游戏目录 |
| 显示补丁 | CreateFont CharSet→GBK、DBCS 范围、GetACP=936 |
| 启动器 | 生成 `点我启动_中文汉化版.bat` + `汉化启动说明.txt` |

**玩法注意（很重要）：**

- 用 **`点我启动_中文汉化版.bat`** 启动，**不要**用 Locale Emulator「日语运行」  
- 汉化后尽量 **新游戏**；卡在半截场景的旧存档可能对不上  
- 个别句子可能略短（槽位夹紧），为了能通关  

工作目录：`游戏目录\_galautotl_lcse\`

若桌面备份里已经不是原版（曾覆盖），请先自行还原真·原版封包/exe 再跑一键汉化。

### Kirikiri / XP3（`.xp3` + `.ks`）— 一键补丁

1. **游戏根目录**选到含 `.xp3`（或已有明文 `.ks`）的文件夹  
2. 点 **探测引擎**（切到 Kirikiri）或手动选 **Kirikiri / XP3（一键补丁）**  
3. 源语言 **日文**，填 API Key → **开始汉化**

自动完成：

| 步骤 | 作用 |
|------|------|
| 备份 | 桌面 `自动翻译备份\kirikiri_游戏名\` |
| 解包 | 从无加密 XP3 解出 `.ks`（已有明文则直接用） |
| AI 精翻 | 对白行 + 邻行上下文；专名表；`[p]`/`\p` 遮罩；导出 `GalAutoTL_review.txt` 可校对灌回 |
| 部署 | `patch.xp3` + `cn_scenario/` + `AfterInit2.tjs` / `Config.tjs` 挂钩 |

**注意：**

- 多数现代 Kirikiri 为 **UTF-16**，不做 CP932 夹紧  
- 解包已尽量自动化：明文 XP3 → 常见 XOR（Neko 系）→ `FE FE` 脚本还原 → 若本机有 **garbro-cli**（PATH / 工具目录 / 环境变量 `GARBRO`）则自动调用  
- 仍无法解的厂商 **cxdec** 专用密钥：请安装 garbro-cli，或手动解出 `.ks` 填到「文本文件夹」  
- 工作目录：`游戏目录\_galautotl_kirikiri\`

### YU-RIS（`.ypf` / `.ybn`）— 一键注入

1. 游戏根目录选到含 `.ypf` 或已有 `ysbin/*.ybn` 的文件夹  
2. 探测 → **YU-RIS / YBN**，源语言日文 → 开始汉化  

自动：解密 YSTB（自动 XOR 密钥）→ AI 译对白 → 写松散 `.ybn`（引擎优先读磁盘）。  
中文按 **GBK** 写入。仅有 YPF 时需 **garbro-cli**。若进游戏仍乱码，需另打 YU-RIS 的 GBK 显示补丁。  
工作目录：`_galautotl_yuris\`

### Unity（含 IL2CPP / 无 StreamingAssets）

选游戏根目录 → 开始汉化即可（自动识别 Unity / IL2CPP，依赖自动装）。

自动覆盖：
- **稳定注入（默认）**：自动安装 **BepInEx + XUnity.AutoTranslator**，用 AI 译文生成替换表，运行时钩子显示中文（**不改 data.unity3d**）
- 用「点我启动_中文汉化_Unity.bat」启动
- 译文表：`BepInEx\Translation\zh-CN\Text\GalAutoTL.txt`
- **Hazy / AdvScript 类包**（如部分 StreamingAssets `a###`）：采文写回、保留点击等待标签、选项 `ssei`、行号泄漏清理等会在译后自动收尾
- StreamingAssets 明文（若有）仍会直接写回
- **加密包 / Addressables**：扫描 `*.bundle` / `aa\` 等；自动尝试 **文件头 XOR** 与 **UnityCN** 密钥；也可放 `GalAutoTL_unity_ab_key.txt`（或环境变量 `GALAUTOTL_UNITY_AB_KEY`）
- 若曾写坏过包体：用 `data.unity3d.galautotl.bak` 还原
- **方框 □**：运行时会从 [XUA Releases](https://github.com/bbepis/XUnity.AutoTranslator/releases) 拉取 TMP CJK 字体包（本仓库**不附带**字体文件）；也可自行放到 `tools/unity_runtime/` 离线使用，详见该目录 README

工作目录：`_galautotl_unity\`（含 `ab_dec\` 解密缓存）

### Artemis（`.pfs` + `.ast`）— 一键注入

解 pf6/pf8（含 SHA-1 XOR）→ 译 `.ast` 对白 → 松散 `script/` 覆盖（无需回封）。  
**防叠字：** `name=` 只保留短角色名（名字层），整句台词只写正文层；译后对照 JP 还原名框。  
编译型 `.asb` 支持有限。工作目录：`_galautotl_artemis\`

### BGI / Ethornell（`data*.arc`）— 一键

用 garbro-cli 解 ARC → 识别 `BurikoCompiledScript` 剧情脚本 → AI 译 → 写回指针并追加字串池。  
完整副本在 `cn_bgi_scripts\`；若游戏仍读封包需自行回封。`._bp` 不改。工作目录：`_galautotl_bgi\`

### SakanaGL（`.sx` / `.sxstorage`）— 一键

如 `IsekaiHaremSaver`：解 `SSXXDEFL` 索引 → 解密/解压条目 → AI 译文本 → **槽位夹紧**写回 `.sxstorage`。  
依赖 `zstandard`。过长译文可能无法塞回原槽（保留日文）。工作目录：`_galautotl_sakana\`

### 古早 RealLive — 一键

选含 `SEEN.TXT` 的游戏根目录即可。无 `_tools/export_utf8` 时会**自动下载 RLDev/kprl** 解包再翻译（Coming×Humming 经验已并入）。  
译文写到 `_tools/patch_work/cn_utf8`；若 `_tools` 里有 `full_patch.py` 可尝试自动写回。

**译文质量（多引擎通用，Coming×Humming 实战固化）：**

| 阶段 | RealLive | Kirikiri | Unity | Artemis | LCSE / BGI / YU-RIS / Sakana | 通用文本 |
|------|----------|----------|-------|---------|------------------------------|----------|
| 翻译时 `translate_batch` 润色 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| 写回后二次扫文本润色 | ✓ utf | ✓ ks | ✓ 词典 | ✓ ast | ✓ 工作区明文 | ✓ |
| 「仅润色译文」 | ✓ | ✓ | ✓ | ✓ | 仅明文导出；二进制槽需重跑管线 | ✓ |

- 规则：残留假名、`朋友达`、`此家辈`、`0计`、粗糙刷屏、选项机翻腔等
- 高级选项可关「机翻后处理润色」
- 二进制正文（LCSE SNX、BGI 字串池、YBN、Sakana 槽）在**翻译写入时**已润色；仅润色扫不到包内字符串时请重新「开始汉化」
- RealLive 优先 `sjis_ext`+代理，勿轻易勾 CP932 改字；改 `SEEN.TXT` 后建议新周目

### API

默认 DeepSeek：`https://api.deepseek.com` / `deepseek-v4-flash`（或 `deepseek-v4-pro`）  
旧名 `deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 停用。  
设置：`%APPDATA%\GalAutoTL\config.json`  
缓存：`%APPDATA%\GalAutoTL\cache.sqlite`

### 漏句闭环 / 图片 UI

- 汉化后游戏目录生成 `GalAutoTL_remain.txt`（按 dialogue/ui/sfx/path 分类）  
- 点 **仅译漏句**：只翻译 remain 里仍漏的句子（需先完整汉化一次）  
- 点 **图片UI清单**：扫描 `graphic=` / `storage=` 等，写出 `GalAutoTL_image_ui.txt`（改像素字仍靠手工）

## 组合使用的外部项目

本工具是编排/写回层，运行时会调用或下载下列上游（链接便于自行核对许可与版本）。**不是**它们的官方发行版。

| 项目 | 用途 |
|------|------|
| [BepInEx](https://github.com/BepInEx/BepInEx) | Unity 注入框架（Mono / IL2CPP）；一键时自动下载安装 |
| [XUnity.AutoTranslator](https://github.com/bbepis/XUnity.AutoTranslator) | Unity 运行时词典替换；写 `GalAutoTL.txt`，并拉取其 Release 中的 TMP CJK 字体包治 □ |
| [Il2CppDumper](https://github.com/Perfare/Il2CppDumper) | IL2CPP `stringliteral.json` 采文（按需下载） |
| [unity-libs（BepInEx）](https://unity.bepinex.dev/) | IL2CPP 基库 zip，避免首次卡在 Downloading unity base libraries |
| [ManlyMarco / BruteForceFix](https://github.com/ManlyMarco/RandomPlugins) | XUA IL2CPP 补扫插件（会尝试安装；新 Interop 上常自动禁用） |
| [KirikiriTools](https://github.com/arcusmaximus/KirikiriTools) | 自动部署 `version.dll`；`FE FE` 脚本还原逻辑参考其 Descrambler |
| [GARbro](https://github.com/morkt/GARbro) / garbro-cli | 外调解加密封包（Kirikiri cxdec、YPF、部分 PFS/ARC 等）；需本机自备 |
| [VNTranslationTools](https://github.com/arcusmaximus/VNTranslationTools) | YU-RIS YSTB 密钥/布局参考 |
| [YuriSizuku/GalgameReverse](https://github.com/YuriSizuku/GalgameReverse)（artemis pf8）+ GARbro ArcPFS | Artemis PFS 解包实现参考 |
| GARbro ArcSX（同上 GARbro 仓库） | SakanaGL `.sx` 索引解析移植 |
| [UnityPy](https://github.com/K0lb3/UnityPy) | Unity 资源读写（pip） |
| [TypeTreeGeneratorAPI](https://pypi.org/project/TypeTreeGeneratorAPI/) | IL2CPP MonoBehaviour 结构化采文（启动时可自动 pip） |
| [PySide6](https://doc.qt.io/qtforpython/) | 图形界面 |
| [PyInstaller](https://pyinstaller.org/) | 可选打单文件 EXE |
| [zstandard](https://pypi.org/project/zstandard/) | SakanaGL 条目解压 |
| DeepSeek / OpenAI 兼容 API | 批量机翻（自备 Key；默认 `api.deepseek.com`） |

另：RealLive 等可选用你自备的 VNTextProxy / rldev / VNTextPatch（见 `tools/README.txt`），本程序不捆绑。下载 GitHub 资源时可能经 `ghfast.top` 镜像加速。

## 注意

- 翻译消耗 API 额度。  
- 请自行确认游戏版权与使用范围；本工具开源免费，仅辅助个人汉化学习，不附带任何游戏资源或完整汉化包。  
- 上表项目由程序按需下载、外调或本机放置；字形 / 运行时二进制等授权以各上游为准，本仓库不附带字体包与 BepInEx/XUA zip。  
- 许可证见仓库根目录 `LICENSE`（MIT）。
