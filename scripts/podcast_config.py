#!/usr/bin/env python3
"""対象ポッドキャストの設定とパスを提供する。

設定は `config/podcast.md` の frontmatter（`key: value` のみ）に置く。
データの場所は環境変数 `PODCAST_DATA_DIR` で差し替えできる。
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "podcast.md"
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "podcast.md"

DATA_DIR = Path(os.environ.get("PODCAST_DATA_DIR", PROJECT_ROOT / "data"))
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
PRIVATE_DIR = DATA_DIR / "private"
CSV_PATH = PRIVATE_DIR / "episodes.csv"
KNOWLEDGE_DIR = PRIVATE_DIR / "knowledge"

SETUP_HINT = (
    f"セットアップがまだです。\n"
    f"  cp {TEMPLATE_PATH.relative_to(PROJECT_ROOT)} {CONFIG_PATH.relative_to(PROJECT_ROOT)}\n"
    f"を実行し、対象のポッドキャストに合わせて編集してください。詳しくは README.md を参照。"
)


def read_frontmatter(path=CONFIG_PATH):
    """`---` で囲まれた frontmatter を dict で返す。`key: value` のみ対応。"""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    values = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip()] = value.strip()
    return values


def load_config(required=()):
    """設定を読む。未セットアップや必須キー欠落なら案内を出して終了する。"""
    if not CONFIG_PATH.exists():
        print(SETUP_HINT, file=sys.stderr)
        raise SystemExit(1)

    config = read_frontmatter()
    missing = [key for key in required if not config.get(key)]
    if missing:
        rel = CONFIG_PATH.relative_to(PROJECT_ROOT)
        print(f"エラー: {rel} の frontmatter に {', '.join(missing)} がありません", file=sys.stderr)
        raise SystemExit(1)
    return config


def require_csv():
    """episodes.csv の存在を確認する。無ければ作り方を案内して終了する。"""
    if not CSV_PATH.exists():
        rel = CSV_PATH.relative_to(PROJECT_ROOT)
        print(
            f"エラー: {rel} がありません。\n"
            f"  python3 scripts/sync_episodes.py --apply\n"
            f"でRSSから作成してください。",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return CSV_PATH
