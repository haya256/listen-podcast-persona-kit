#!/usr/bin/env python3
"""指定エピソードの文字起こし(VTT)をLISTENから取得する。

使い方:
    python3 scripts/fetch_transcripts.py 20260705-073500           # 公開日時キーで指定
    python3 scripts/fetch_transcripts.py "2026-07-05 07:35:00"     # 公開日時(CSV表記)でも可
    python3 scripts/fetch_transcripts.py bbosrqmx                  # URL末尾のslugでも可
    python3 scripts/fetch_transcripts.py 20260705-073500 bbosrqmx  # 複数指定

- 指定された回は取得済み(あり)でも再取得して上書きする
- フォロワー限定回はスキップ(認証なしでは404のため)
- 取得結果に応じて episodes.csv の「文字起こし」列を更新する
"""

import sys
import time
import urllib.error
import urllib.request

from episodes_csv import (
    COL_FOLLOWERS_ONLY,
    COL_PAID,
    COL_PUBLISHED_AT,
    COL_TITLE,
    COL_TRANSCRIPT,
    COL_URL,
    key_of,
    load_rows,
    save_rows,
    slug_of,
)
from podcast_config import PROJECT_ROOT, TRANSCRIPTS_DIR, require_csv

REQUEST_INTERVAL_SEC = 3


def find_row(rows, key):
    for row in rows:
        if key in (row[COL_PUBLISHED_AT], key_of(row), slug_of(row)) and key:
            return row
    return None


def fetch_vtt(url):
    """(status_code, body) を返す。404は(404, None)。"""
    try:
        with urllib.request.urlopen(url, timeout=30) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, None


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip())
        return 0

    require_csv()
    rows, fieldnames = load_rows()
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    fetched, missing, skipped, errors = [], [], [], []
    need_sleep = False

    for key in args:
        row = find_row(rows, key)
        if row is None:
            print(f"エラー: エピソード「{key}」が episodes.csv に見つかりません")
            errors.append(key)
            continue

        label = f"{row[COL_TITLE]}（{row[COL_PUBLISHED_AT] or slug_of(row)}）"
        if row[COL_FOLLOWERS_ONLY] == "TRUE":
            print(f"スキップ: {label} はフォロワー限定回のため未対応です")
            skipped.append(key)
            continue
        if row.get(COL_PAID):
            print(f"スキップ: {label} は有料回（{row[COL_PAID]}）のため公開の文字起こしからは取得できません")
            skipped.append(key)
            continue

        if need_sleep:
            time.sleep(REQUEST_INTERVAL_SEC)
        need_sleep = True

        vtt_url = row[COL_URL].rstrip("/") + "/transcript.vtt"
        status, body = fetch_vtt(vtt_url)
        if status == 200 and body:
            out_path = TRANSCRIPTS_DIR / f"{slug_of(row)}.vtt"
            out_path.write_bytes(body)
            row[COL_TRANSCRIPT] = "あり"
            print(f"取得: {label} → {out_path.relative_to(PROJECT_ROOT)} ({len(body)} bytes)")
            fetched.append(key)
        elif status == 404:
            row[COL_TRANSCRIPT] = "なし"
            print(f"なし: {label} は文字起こしが存在しません (404)")
            missing.append(key)
        else:
            print(f"警告: {label} の取得に失敗しました (HTTP {status})。状態は変更しません")
            errors.append(key)

    if fetched or missing:
        save_rows(rows, fieldnames)

    print(f"\n結果: 取得 {len(fetched)} / なし {len(missing)} / スキップ {len(skipped)} / エラー {len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
