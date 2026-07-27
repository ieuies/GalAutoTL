# -*- coding: utf-8 -*-
"""Local settings under %APPDATA%/GalAutoTL/config.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_NAME = "GalAutoTL"


def appdata_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return appdata_dir() / "config.json"


def cache_db_path() -> Path:
    return appdata_dir() / "cache.sqlite"


@dataclass
class AppConfig:
    game_dir: str = ""
    text_dir: str = ""
    tools_dir: str = ""
    pipeline: str = "auto"  # auto-detect on start
    api_base: str = "https://api.deepseek.com"
    api_key: str = ""
    api_model: str = "deepseek-v4-flash"
    temperature: float = 0.3
    batch_size: int = 24  # faster for normal UTF games
    lang: str = "zh_cn"  # target: zh_cn | zh_tw
    source_lang: str = "auto"  # auto | ja | en | ko | ru | other
    cp932_safe: bool = False  # only for old JP-OS / RealLive
    mt_polish: bool = True  # post-MT polish (达/此/选项/假名残留等)
    do_backup: bool = True
    simple_mode: bool = True
    auto_copy_font: bool = True  # copy CJK font into game dir before translate
    unity_patch_metadata: bool = False  # IL2CPP metadata write (often crashes)
    # Writing data.unity3d (UnityFS rebuild) hangs/blackscreens many Unity 202X titles on Start.
    unity_patch_assets: bool = False
    ui_font: str = ""  # empty = auto (Microsoft YaHei / 微软雅黑)
    extra: dict[str, Any] = field(default_factory=dict)

    def save(self) -> None:
        data = asdict(self)
        config_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "AppConfig":
        path = config_path()
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore
        kwargs = {k: v for k, v in raw.items() if k in known}
        return cls(**kwargs)
