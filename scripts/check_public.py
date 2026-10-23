#!/usr/bin/env python3
"""公開リポジトリに番組固有の情報が混入していないか検査する。

使い方:
    python3 scripts/check_public.py

- 検査語は config/podcast.md から集める（このスクリプト自身に番組名を書かない）
- git が追跡しているファイルだけを見る（config/ data/ pmo/ は .gitignore 済み）
- 1件でも見つかれば終了コード 1
"""

import subprocess
import sys

from podcast_config import CONFIG_PATH, PROJECT_ROOT, load_config

# 検査から除外するファイル（設定そのものと、このスクリプト）
EXCLUDED = {"scripts/check_public.py"}


def search_terms():
    """config から検査語を組み立てる。URLからは番組slugも取り出す。"""
    config = load_config()
    terms = set()
    for key in ("name", "host"):
        value = config.get(key, "").strip()
        if value:
            terms.add(value)

    listen_url = config.get("listen_url", "").strip()
    if listen_url:
        terms.add(listen_url)
        slug = listen_url.rstrip("/").rsplit("/", 1)[-1]
        if slug:
            terms.add(slug)
    return sorted(terms, key=len, reverse=True)


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("エラー: git リポジトリではないか、git ls-files に失敗しました", file=sys.stderr)
        raise SystemExit(1)
    return [p for p in result.stdout.split("\0") if p and p not in EXCLUDED]


def main():
    terms = search_terms()
    if not terms:
        rel = CONFIG_PATH.relative_to(PROJECT_ROOT)
        print(f"エラー: {rel} から検査語を取り出せませんでした", file=sys.stderr)
        return 1

    hits = []
    for path in tracked_files():
        full = PROJECT_ROOT / path
        for term in terms:
            if term in path:
                hits.append((path, 0, f"ファイル名に含まれています: {path}"))
        try:
            text = full.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(term in line for term in terms):
                hits.append((path, lineno, line.strip()))

    print(f"検査語 {len(terms)} 個 / 追跡ファイル {len(tracked_files())} 件")
    if not hits:
        print("混入なし。公開して問題ありません")
        return 0

    print(f"\n{len(hits)} 件みつかりました:")
    for path, lineno, text in hits:
        where = f"{path}:{lineno}" if lineno else path
        print(f"  {where}  {text[:100]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
