#!/usr/bin/env python3
"""指定エピソードの文字起こし(VTT)をLISTENから取得する。

使い方:
    python3 scripts/fetch_transcripts.py 20260705-073500           # 公開日時キーで指定
    python3 scripts/fetch_transcripts.py "2026-07-05 07:35:00"     # 公開日時(CSV表記)でも可
    python3 scripts/fetch_transcripts.py bbosrqmx                  # URL末尾のslugでも可
    python3 scripts/fetch_transcripts.py 20260705-073500 bbosrqmx  # 複数指定
    python3 scripts/fetch_transcripts.py 20260705-073500 --no-mcp  # MCP を使わずに取る

- 取り方は次の順に選ぶ
  1. LISTEN MCP Server（LISTEN の公式ツール）が使える環境（環境変数 LISTEN_API_TOKEN と npx がある）なら、
     それで取る（scripts/listen_mcp.py）。番組の持ち主のトークンなら、フォロワー限定回・有料回も取れる
  2. 使えなければ、公開の transcript.vtt を取る。404 なら、エピソードページの HTML に埋め込まれた
     文字起こしから VTT を組み立てる（番組によっては VTT の URL が公開されず、ページにだけ表示されるため）。
     この方法ではフォロワー限定回・有料回はスキップする
- 指定された回は取得済み(あり)でも再取得して上書きする
- 取得結果に応じて episodes.csv の「文字起こし」列を更新する
- 403・429 などのエラーが出たら、再試行せずにそこで止める
"""

import argparse
import html
import re
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
from podcast_config import PROJECT_ROOT, TRANSCRIPTS_DIR, USER_AGENT, require_csv

REQUEST_INTERVAL_SEC = 3

STOP_HINT = (
    "自動での再試行はしません。原因を探るために、User-Agent などを変えて再アクセスすることもしないでください\n"
    "（アクセスを受けた側からは、不正アクセスの試みに見えるおそれがあります）。どうするかは利用者が判断してください。"
)


def find_row(rows, key):
    for row in rows:
        if key in (row[COL_PUBLISHED_AT], key_of(row), slug_of(row)) and key:
            return row
    return None


def fetch_url(url):
    """(status_code, body) を返す。HTTP エラーなら (コード, None)。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as e:
        return e.code, None


# 区間の書き方は2通りある（古い: x-data="{ start: 1.2, end: 3.4 }"、新しい: data-start="1.2" data-end="3.4"）
SEGMENT_RE = re.compile(
    r'<div id="t[^"]*"[^>]*?'
    r'(?:x-data="\{ start: ([\d.]+), end: ([\d.]+) \}"|data-start="([\d.]+)"\s+data-end="([\d.]+)")'
    r'[^>]*>(.*?)</div>',
    re.S,
)


def vtt_time(sec):
    ms = round(float(sec) * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def vtt_from_html(page_html):
    """エピソードページの HTML に埋め込まれた文字起こしを VTT（bytes）にする。なければ None。"""
    cues = []
    for s1, e1, s2, e2, inner in SEGMENT_RE.findall(page_html):
        start, end = s1 or s2, e1 or e2
        text = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        if text:
            cues.append(f"{vtt_time(start)} --> {vtt_time(end)}\n{text}\n")
    if not cues:
        return None
    return ("WEBVTT\n\n" + "\n".join(cues)).encode("utf-8")


def fetch_from_page(rows, fieldnames, keys):
    """MCP を使わない取り方（公開の transcript.vtt → エピソードページ）。"""
    fetched, missing, skipped, errors = [], [], [], []
    need_sleep = False

    for key in keys:
        row = find_row(rows, key)
        if row is None:
            print(f"エラー: エピソード「{key}」が episodes.csv に見つかりません")
            errors.append(key)
            continue

        label = f"{row[COL_TITLE]}（{row[COL_PUBLISHED_AT] or slug_of(row)}）"
        if row[COL_FOLLOWERS_ONLY] == "TRUE":
            print(f"スキップ: {label} はフォロワー限定回のため、この方法では取れません")
            skipped.append(key)
            continue
        if row.get(COL_PAID):
            print(f"スキップ: {label} は有料回（{row[COL_PAID]}）のため、この方法では取れません")
            skipped.append(key)
            continue

        if need_sleep:
            time.sleep(REQUEST_INTERVAL_SEC)
        need_sleep = True

        vtt_url = row[COL_URL].rstrip("/") + "/transcript.vtt"
        status, body = fetch_url(vtt_url)
        source, failed_url = "", vtt_url
        if status == 404:
            time.sleep(REQUEST_INTERVAL_SEC)
            status, page = fetch_url(row[COL_URL])
            failed_url = row[COL_URL]
            body = vtt_from_html(page.decode("utf-8")) if status == 200 and page else None
            if status == 200:
                status, source = (200, "（ページから）") if body else (404, "")

        if status == 200 and body:
            out_path = TRANSCRIPTS_DIR / f"{slug_of(row)}.vtt"
            out_path.write_bytes(body)
            row[COL_TRANSCRIPT] = "あり"
            print(f"取得{source}: {label} → {out_path.relative_to(PROJECT_ROOT)} ({len(body)} bytes)")
            fetched.append(key)
        elif status == 404:
            row[COL_TRANSCRIPT] = "なし"
            print(f"なし: {label} は文字起こしが存在しません（VTT は 404、ページにもなし）")
            missing.append(key)
        else:
            print(f"エラー: {label} の取得に失敗しました（{failed_url} が HTTP {status}）。状態は変更しません")
            print(STOP_HINT)
            errors.append(key)
            break

    if fetched or missing:
        save_rows(rows, fieldnames)

    print(f"\n結果: 取得 {len(fetched)} / なし {len(missing)} / スキップ {len(skipped)} / エラー {len(errors)}")
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("keys", nargs="*", help="公開日時キー・CSVの公開日時表記・slug")
    parser.add_argument("--no-mcp", action="store_true", help="LISTEN MCP Server を使わずに取る")
    args = parser.parse_args()
    if not args.keys:
        print(__doc__.strip())
        return 0

    require_csv()
    rows, fieldnames = load_rows()
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    if not args.no_mcp:
        import listen_mcp

        if listen_mcp.available():
            print("LISTEN MCP Server で取得します")
            return listen_mcp.fetch_transcripts(rows, fieldnames, args.keys)
        print(f"LISTEN MCP Server は使えないので、公開の文字起こしから取得します（{listen_mcp.unavailable_reason()}）")

    return fetch_from_page(rows, fieldnames, args.keys)


if __name__ == "__main__":
    sys.exit(main())
