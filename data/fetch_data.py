#!/usr/bin/env python3
"""
fetch_data — 下載 kanpan 需要的資料（不進 git，大檔放 GitHub Release / 來源 repo）

1. kline_deep.json（80MB，2021-06~2024-12 深歷史K）
   來源：tw-stock-bot Release `kline-deep-2021-2024` → 歷史驗證用
2. kline_cache.json.gz（~6MB，近期日K 到最新）
   來源：tw-stock-bot repo raw → 面板現況用

用法：python fetch_data.py [deep|cache|all]
"""
import os
import ssl
import sys
import json
import urllib.request
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
CTX = ssl.create_default_context()
REPO = "jx0876/tw-stock-bot"

# repo 私有 → Release/raw 下載都要 token（自動從 stock-secrets 找，同 vp_brief 模式）
def _find_token() -> str:
    k = os.getenv("GH_PAT") or os.getenv("GITHUB_TOKEN") or ""
    if k:
        return k
    cands = [os.getenv("STOCK_SECRETS_DIR"),
             str(Path.home() / "Desktop" / "Justin" / "stock-secrets"),
             "/Users/justin/Desktop/Justin/stock-secrets",
             str(Path.home() / "stock-secrets")]
    for d in cands:
        if not d:
            continue
        f = Path(d) / "股票用bot.env"
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                for key in ("GH_PAT=", "GITHUB_TOKEN="):
                    if s.startswith(key):
                        return s.split("=", 1)[1].strip().strip('"')
    return ""


TOKEN = _find_token()

# deep 走 Release asset API（私庫 browser url 會 404）；cache 走 raw+token
ASSETS = {"deep": ("kline-deep-2021-2024", "kline_deep.json",
                   os.path.join(HERE, "kline_deep.json")),
          "revenue": ("revenue-2019-2026", "revenue.json",
                      os.path.join(HERE, "revenue.json"))}
RAWS = {"cache": (f"https://raw.githubusercontent.com/{REPO}/main/cache/kline_cache.json.gz",
                  os.path.join(HERE, "..", "cache", "kline_cache.json.gz"))}


def _get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=600, context=CTX)


def _save(resp, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    total = 0
    with open(dst, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
            print(f"\r  {total/1e6:.1f} MB", end="")
    print(f"\n  → {dst}")


def fetch(name):
    hdr = {"User-Agent": "Mozilla/5.0"}
    if TOKEN:
        hdr["Authorization"] = f"token {TOKEN}"
    if name in ASSETS:
        tag, fname, dst = ASSETS[name]
        # 私庫：查 asset id → 用 API url + octet-stream 下載
        with _get(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}", hdr) as r:
            rel = json.loads(r.read().decode("utf-8"))
        asset = next(a for a in rel["assets"] if a["name"] == fname)
        print(f"下載 {name}: {fname} ({asset['size']/1e6:.0f} MB)")
        dl_hdr = dict(hdr); dl_hdr["Accept"] = "application/octet-stream"
        with _get(asset["url"], dl_hdr) as r:
            _save(r, dst)
    else:
        url, dst = RAWS[name]
        print(f"下載 {name}: {url}")
        with _get(url, hdr) as r:
            _save(r, dst)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    for k in (["deep", "cache"] if which == "all" else [which]):
        fetch(k)
