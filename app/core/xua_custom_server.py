# -*- coding: utf-8 -*-
"""Local HTTP endpoint for XUnity.AutoTranslator CustomTranslate.

XUA calls: GET {Url}?from=ja&to=zh-CN&text=...
Response body: plain translated string only.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, unquote_plus, urlparse

DEFAULT_PORT = 8765


def _load_config() -> dict:
    cfg_path = Path.home() / "AppData" / "Roaming" / "GalAutoTL" / "config.json"
    if cfg_path.is_file():
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    return {}




def _split_dict_line(line: str) -> tuple[str, str]:
    """Split on first unescaped '=' (XUnity uses {{=}} in keys/values)."""
    i = 0
    while i < len(line):
        if line.startswith("{{=}}", i):
            i += 5
            continue
        if line[i] == "=":
            return line[:i], line[i + 1 :]
        i += 1
    return line, ""


def _unescape_dict_text(s: str) -> str:
    return s.replace("{{=}}", "=").replace("\\n", "\n")


def _escape_dict_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("=", "{{=}}")
    return s.replace("\n", "\\n")


def _galautotl_dict_path(game_dir: Path) -> Path:
    return game_dir / "BepInEx" / "Translation" / "zh-CN" / "Text" / "GalAutoTL.txt"


_galautotl_append_lock = threading.Lock()


def _append_galautotl_line(game_dir: Path, key: str, value: str) -> None:
    path = _galautotl_dict_path(game_dir)
    line = f"{_escape_dict_text(key)}={_escape_dict_text(value)}\n"
    with _galautotl_append_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line)


def _load_dict(game_dir: Path) -> Dict[str, str]:
    d: Dict[str, str] = {}
    p = game_dir / "BepInEx" / "Translation" / "zh-CN" / "Text" / "GalAutoTL.txt"
    if not p.is_file():
        return d
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = _split_dict_line(line)
        k = _unescape_dict_text(k)
        v = _unescape_dict_text(v)
        if k and v:
            d[k] = v
    return d


def _cache_get(src: str, lang: str, model: str) -> Optional[str]:
    db = Path.home() / "AppData" / "Roaming" / "GalAutoTL" / "cache.sqlite"
    if not db.is_file():
        return None
    import hashlib

    key = hashlib.sha256(f"ja|{lang}|{model}|{src}".encode("utf-8")).hexdigest()
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT dst FROM cache WHERE key=?", (key,)).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _cache_put(src: str, dst: str, lang: str, model: str) -> None:
    db = Path.home() / "AppData" / "Roaming" / "GalAutoTL" / "cache.sqlite"
    import hashlib

    key = hashlib.sha256(f"ja|{lang}|{model}|{src}".encode("utf-8")).hexdigest()
    try:
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT OR REPLACE INTO cache(key, src, dst, lang) VALUES(?,?,?,?)",
            (key, src, dst, f"ja->{lang}"),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _ai_translate(text: str, cfg: dict) -> str:
    # late import so server stays light
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from app.core.api_client import OpenAICompatClient

    client = OpenAICompatClient(
        cfg.get("api_base") or "https://api.deepseek.com",
        cfg.get("api_key") or "",
        cfg.get("api_model") or "deepseek-v4-flash",
        float(cfg.get("temperature") or 0.3),
    )
    lang = cfg.get("lang") or "zh_cn"
    tgt = "简体中文" if str(lang).startswith("zh") else "繁体中文"
    prompt = (
        f"将下列日文翻译成{tgt}。只输出译文，不要解释，保留占位符和HTML标签：\n{text}"
    )
    out = client.chat("你是专业游戏本地化译者", prompt)
    return (out or "").strip()


class _Handler(BaseHTTPRequestHandler):
    game_dir: Path = Path(".")
    cfg: dict = {}
    dictionary: Dict[str, str] = {}
    lock = threading.Lock()

    def log_message(self, fmt: str, *args) -> None:  # quieter
        sys.stderr.write("XUA-proxy: " + (fmt % args) + "\n")

    def do_GET(self) -> None:
        q = parse_qs(urlparse(self.path).query)
        text = unquote_plus((q.get("text") or [""])[0])
        if not text:
            self._respond(400, "")
            return
        # exact static first
        hit = self.dictionary.get(text)
        if hit:
            self._respond(200, hit)
            return
        lang = self.cfg.get("lang") or "zh_cn"
        model = self.cfg.get("api_model") or "deepseek-v4-flash"
        cached = _cache_get(text, lang, model)
        if cached:
            self._respond(200, cached)
            return
        if not self.cfg.get("api_key"):
            self._respond(200, text)  # passthrough
            return
        try:
            with self.lock:
                # re-check cache under lock
                cached = _cache_get(text, lang, model)
                if cached:
                    self._respond(200, cached)
                    return
                dst = _ai_translate(text, self.cfg)
            if not dst:
                dst = text
            _cache_put(text, dst, lang, model)
            with self.lock:
                if text not in self.dictionary:
                    _append_galautotl_line(self.game_dir, text, dst)
                self.dictionary[text] = dst
            self._respond(200, dst)
        except Exception as e:
            sys.stderr.write(f"XUA-proxy translate error: {e}\n")
            self._respond(200, text)

    def _respond(self, code: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(game_dir: Path, port: int = DEFAULT_PORT) -> None:
    cfg = _load_config()
    _Handler.game_dir = game_dir
    _Handler.cfg = cfg
    _Handler.dictionary = _load_dict(game_dir)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"GalAutoTL XUA proxy http://127.0.0.1:{port}/  dict={len(_Handler.dictionary)}", flush=True)
    httpd.serve_forever()


def main() -> None:
    game = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT
    serve(game, port)


if __name__ == "__main__":
    main()
