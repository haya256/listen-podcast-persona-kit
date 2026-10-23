#!/usr/bin/env python3
"""LISTEN の GraphQL API で、RSS では取れない情報を扱う（応用編）。

使い方:
    LISTEN_API_TOKEN=<トークン> python3 scripts/listen_api.py sync                # ドライラン
    LISTEN_API_TOKEN=<トークン> python3 scripts/listen_api.py sync --apply        # 実際に書き込む
    LISTEN_API_TOKEN=<トークン> python3 scripts/listen_api.py transcript 20260705-073500  # 文字起こし取得

- トークンは LISTEN の「API Tokens」設定ページで発行する。なくても他のスクリプトは動く
- API は読み取り（query）だけを使う

sync:
- 対象の番組は、トークンの持ち主の番組から config/podcast.md の name で探す
- 公開済みの回だけを取得する（下書き・予約投稿は見ない）
- 反映するのは、公開日時が空の行の日時補完と、RSS に載らない回（フォロワー限定回など）の追加だけ
- 既存行のフォロワー限定・有料の列は書き換えず、API と食い違えば警告を出す

transcript:
- 指定の仕方は fetch_transcripts.py と同じ（公開日時キー・CSVの公開日時表記・slug。複数指定可）
- 番組の持ち主のトークンなら、フォロワー限定回・有料回の文字起こしも取れる
- 取得済みでも再取得して上書きし、episodes.csv の「文字起こし」列を更新する
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta

from episodes_csv import (
    COL_EPISODE_NO,
    COL_FOLLOWERS_ONLY,
    COL_PAID,
    COL_PUBLISHED_AT,
    COL_PUBLISHED_ON,
    COL_TAGS,
    COL_TITLE,
    COL_TRANSCRIPT,
    COL_URL,
    load_rows,
    save_rows,
    slug_of,
)
from fetch_transcripts import find_row
from podcast_config import CSV_PATH, PROJECT_ROOT, TRANSCRIPTS_DIR, load_config, require_csv
from sync_episodes import sort_key

GRAPHQL_URL = os.environ.get("LISTEN_GRAPHQL_URL", "https://listen.style/graphql")
PER_PAGE = 50
REQUEST_INTERVAL_SEC = 1

TOKEN_HINT = (
    "LISTEN_API_TOKEN が設定されていません。\n"
    "このスクリプトは応用編で、なくても他の機能は使えます。使う場合は LISTEN の\n"
    "「API Tokens」設定ページでトークンを発行し、環境変数 LISTEN_API_TOKEN に入れて実行してください。"
)

EPISODES_QUERY = """
query($id: String!, $page: Int) {
  podcast(id: $id) {
    episodes(status: PUBLISHED, sort: OLDEST, first: %d, page: $page) {
      paginatorInfo { hasMorePages }
      data { slug title pubDate visibility price }
    }
  }
}
""" % PER_PAGE

TRANSCRIPT_QUERY = """
query($url: String!) {
  modelByUrl(url: $url) {
    ... on Episode { slug transcriptVtt }
  }
}
"""


def graphql(token, query, variables=None):
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        body = json.load(res)
    if body.get("errors"):
        messages = "; ".join(e.get("message", "") for e in body["errors"])
        raise SystemExit(f"エラー: API がエラーを返しました: {messages}")
    return body["data"]


def normalize_title(title):
    """番組名の突き合わせ用。波ダッシュ（〜）と全角チルダ（～）を同一視する。"""
    return (title or "").replace("～", "〜").strip()


def find_podcast_id(token, name):
    data = graphql(token, "query { me { verifiedPodcasts(first: 100) { data { id title } } } }")
    podcasts = data["me"]["verifiedPodcasts"]["data"]
    hits = [p for p in podcasts if normalize_title(p["title"]) == normalize_title(name)]
    if len(hits) != 1:
        titles = "\n".join(f"  - {p['title']}" for p in podcasts)
        raise SystemExit(
            f"エラー: トークンの持ち主の番組から「{name}」を1つに特定できませんでした"
            f"（該当 {len(hits)} 件）。番組一覧:\n{titles}"
        )
    return hits[0]["id"]


def fetch_episodes(token, podcast_id):
    episodes, page = [], 1
    while True:
        data = graphql(token, EPISODES_QUERY, {"id": podcast_id, "page": page})
        result = data["podcast"]["episodes"]
        episodes += result["data"]
        if not result["paginatorInfo"]["hasMorePages"]:
            return episodes
        page += 1
        time.sleep(REQUEST_INTERVAL_SEC)


def to_jst(pub_date):
    """API の pubDate（UTC の "YYYY-MM-DD HH:MM:SS"）を JST にする。空なら ("", "")。"""
    if not pub_date:
        return "", ""
    jst = datetime.strptime(pub_date, "%Y-%m-%d %H:%M:%S") + timedelta(hours=9)
    return jst.strftime("%Y-%m-%d %H:%M:%S"), jst.strftime("%Y-%m-%d")


def format_price(price):
    return f"¥{price:.0f}" if price else ""


def merge(rows, episodes, listen_url):
    """API の内容を反映する。(追加, 日時補完, 警告) を返す。"""
    by_slug = {slug_of(row): row for row in rows}
    added, dated, warnings = [], [], []

    for ep in episodes:
        # タイトルも公開日時もない回は RSS の不正データと同じ実体のない項目なので無視する
        if not ep["title"] and not ep["pubDate"]:
            continue

        published_at, published_on = to_jst(ep["pubDate"])
        followers_only = ep["visibility"] == "FOLLOWERS_ONLY"
        paid = format_price(ep["price"])
        row = by_slug.get(ep["slug"])

        if row is None:
            row = {
                COL_PUBLISHED_AT: published_at,
                COL_TITLE: ep["title"].strip(),
                COL_PUBLISHED_ON: published_on,
                COL_FOLLOWERS_ONLY: "TRUE" if followers_only else "FALSE",
                COL_PAID: paid,
                COL_TRANSCRIPT: "限定" if followers_only else "未取得",
                COL_URL: f"{listen_url.rstrip('/')}/{ep['slug']}",
                COL_TAGS: "",
                COL_EPISODE_NO: "",
            }
            rows.append(row)
            added.append(row)
            continue

        if published_at and not row[COL_PUBLISHED_AT]:
            row[COL_PUBLISHED_AT] = published_at
            row[COL_PUBLISHED_ON] = published_on
            dated.append(row)

        if (row[COL_FOLLOWERS_ONLY] == "TRUE") != followers_only or row[COL_PAID] != paid:
            warnings.append(
                f"{row[COL_TITLE]}: CSV はフォロワー限定={row[COL_FOLLOWERS_ONLY]} 有料={row[COL_PAID] or 'なし'}、"
                f"API は {ep['visibility']} 有料={paid or 'なし'}"
            )

    rows.sort(key=sort_key)
    return added, dated, warnings


def sync(token, apply_changes):
    config = load_config(required=("name", "listen_url"))
    require_csv()
    rows, fieldnames = load_rows()

    episodes = fetch_episodes(token, find_podcast_id(token, config["name"]))
    print(f"API: 公開済み {len(episodes)} 件")

    before = len(rows)
    added, dated, warnings = merge(rows, episodes, config["listen_url"])

    for row in added:
        print(f"追加: {row[COL_TITLE]}（{row[COL_PUBLISHED_AT] or slug_of(row)}）")
    for row in dated:
        print(f"日時補完: {row[COL_TITLE]} → {row[COL_PUBLISHED_AT]}")
    for message in warnings:
        print(f"警告: {message}（書き換えません）")

    changed = bool(added or dated)
    if not changed:
        print("変更はありません")
    print(f"行数: {before} → {len(rows)}")

    if not changed:
        return 0
    if not apply_changes:
        print("\nドライランです。書き込むには --apply を付けてください")
        return 0

    save_rows(rows, fieldnames)
    print(f"\n{CSV_PATH.relative_to(PROJECT_ROOT)} を更新しました")
    return 0


def transcript(token, keys):
    require_csv()
    rows, fieldnames = load_rows()
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    fetched, missing, errors = [], [], []
    for i, key in enumerate(keys):
        row = find_row(rows, key)
        if row is None:
            print(f"エラー: エピソード「{key}」が episodes.csv に見つかりません")
            errors.append(key)
            continue

        if i:
            time.sleep(REQUEST_INTERVAL_SEC)
        label = f"{row[COL_TITLE]}（{row[COL_PUBLISHED_AT] or slug_of(row)}）"
        data = graphql(token, TRANSCRIPT_QUERY, {"url": row[COL_URL]})
        episode = data["modelByUrl"]
        if not episode or episode.get("slug") != slug_of(row):
            print(f"エラー: {label} を API で見つけられませんでした（{row[COL_URL]}）")
            errors.append(key)
            continue

        vtt = episode["transcriptVtt"]
        if vtt:
            out_path = TRANSCRIPTS_DIR / f"{slug_of(row)}.vtt"
            out_path.write_text(vtt, encoding="utf-8")
            row[COL_TRANSCRIPT] = "あり"
            print(f"取得: {label} → {out_path.relative_to(PROJECT_ROOT)} ({len(vtt.encode())} bytes)")
            fetched.append(key)
        else:
            row[COL_TRANSCRIPT] = "なし"
            print(f"なし: {label} は文字起こしが存在しません")
            missing.append(key)

    if fetched or missing:
        save_rows(rows, fieldnames)

    print(f"\n結果: 取得 {len(fetched)} / なし {len(missing)} / エラー {len(errors)}")
    return 1 if errors else 0


def main():
    args = sys.argv[1:]
    command = args[0] if args else ""
    if command not in ("sync", "transcript") or (command == "transcript" and len(args) < 2):
        print(__doc__.strip())
        return 0

    token = os.environ.get("LISTEN_API_TOKEN", "").strip()
    if not token:
        print(TOKEN_HINT, file=sys.stderr)
        return 1

    if command == "transcript":
        return transcript(token, args[1:])
    return sync(token, "--apply" in args)


if __name__ == "__main__":
    sys.exit(main())
