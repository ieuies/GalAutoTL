# Unity 稳定注入离线包

把下列 zip **原样**放到本目录（或子目录 `unity-libs/`），一键汉化时可跳过 GitHub / unity.bepinex.dev 下载。

## 必需（按游戏类型）

### IL2CPP（如 DeadEndCity）

- `BepInEx-Unity.IL2CPP-win-x64-6.0.0-pre.2.zip`
- `XUnity.AutoTranslator-BepInEx-IL2CPP-*.zip`（或当前 latest）
- `unity-libs/<版本>.zip`  
  例：Unity `2023.1.15f1` → `unity-libs/2023.1.15.zip`  
  下载：https://unity.bepinex.dev/libraries/2023.1.15.zip  
  **不要解压**，整 zip 丢进游戏的 `BepInEx/unity-libs/`

### Mono

- `BepInEx_win_x64_5.4.23.5.zip`
- `XUnity.AutoTranslator-BepInEx-*.zip`（非 IL2CPP）

卡在日志 `Downloading unity base libraries` = 基库没下下来，用上面 `unity-libs` 离线包即可。

## TMP 中文字体（治 □，可选离线）

本仓库**不收录**字体包。一键汉化时会尝试从 XUA Release 下载：

- https://github.com/bbepis/XUnity.AutoTranslator/releases  
  （`TMP_Font_AssetBundles*.zip` / `.7z`）

也可把下载好的压缩包放到本目录，或把解出的 `arialuni_sdf_u2019` 等直接放到游戏根目录。  
字形授权归原字体权利人；本目录 zip **请勿提交到 Git**。
