#!/usr/bin/env python3
"""RSSから episodes.csv を作成・最新化する。

使い方:
    python3 scripts/sync_episodes.py            # ドライラン（差分を表示するだけ）
    python3 scripts/sync_episodes.py --apply    # 実際に書き込む
    python3 scripts/sync_episodes.py --rss <URL> --apply

- 突き合わせのキーはURL末尾のslug（公開日時は空の回があるためキーに使えない）
- RSSから取るのはタイトル・公開日時・URLだけ
- 手動管理の列（フォロワー限定・文字起こし・エピソード番号）は上書きしない
- RSSに無い既存行は削除しない（フォロワー限定回はRSSに出ないため）
"""

import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime

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
    FIELDNAMES,
    load_rows,
    save_rows,
    slug_of,
)
from podcast_config import CSV_PATH, PROJECT_ROOT, load_config

JST = timezone(timedelta(hours=9))
PODCAST_NS = {"podcast": "https://podcastindex.org/namespace/1.0"}


def fetch_rss(url):
    req = urllib.request.Request(url, headers={"User-Agent": "podcast-knowledge-kit"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return res.read()


def parse_items(raw):
    """RSSの各itemを {slug, title, published_at, published_on, url, has_transcript} にする。"""
    channel = ET.fromstring(raw).find("channel")
    items = []
    for item in channel.findall("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue

        pub_date = (item.findtext("pubDate") or "").strip()
        published_at = published_on = ""
        if pub_date:
            jst = parsedate_to_datetime(pub_date).astimezone(JST)
            published_at = jst.strftime("%Y-%m-%d %H:%M:%S")
            published_on = jst.strftime("%Y-%m-%d")

        items.append(
            {
                "slug": link.rstrip("/").rsplit("/", 1)[-1],
                "title": (item.findtext("title") or "").strip(),
                "published_at": published_at,
                "published_on": published_on,
                "url": link,
                "has_transcript": item.find("podcast:transcript", PODCAST_NS) is not None,
            }
        )
    return items


def sort_key(row):
    """公開日時の降順。空欄は末尾にまとめる。"""
    published_at = row[COL_PUBLISHED_AT]
    return (0 if published_at else 1, [-ord(c) for c in published_at])


def merge(rows, items):
    """既存行にRSSの内容を反映する。(追加, タイトル変更, 日時補完) を返す。"""
    by_slug = {slug_of(row): row for row in rows}
    added, retitled, dated = [], [], []

    for item in items:
        row = by_slug.get(item["slug"])
        if row is None:
            rows.append(
                {
                    COL_PUBLISHED_AT: item["published_at"],
                    COL_TITLE: item["title"],
                    COL_PUBLISHED_ON: item["published_on"],
                    COL_FOLLOWERS_ONLY: "FALSE",
                    COL_PAID: "",
                    COL_TRANSCRIPT: "未取得" if item["has_transcript"] else "なし",
                    COL_URL: item["url"],
                    COL_TAGS: "",
                    COL_EPISODE_NO: "",
                }
            )
            added.append(item)
            continue

        if item["title"] and row[COL_TITLE] != item["title"]:
            retitled.append((row[COL_TITLE], item["title"]))
            row[COL_TITLE] = item["title"]

        # 公開日時はRSS側が空のことがあるので、値があるときだけ反映する
        if item["published_at"] and row[COL_PUBLISHED_AT] != item["published_at"]:
            if not row[COL_PUBLISHED_AT]:
                dated.append(item)
            row[COL_PUBLISHED_AT] = item["published_at"]
            row[COL_PUBLISHED_ON] = item["published_on"]

    rows.sort(key=sort_key)
    return added, retitled, dated


def main():
    args = sys.argv[1:]
    apply_changes = "--apply" in args
    rss_url = None
    if "--rss" in args:
        rss_url = args[args.index("--rss") + 1]
    if not rss_url:
        rss_url = load_config(required=("rss_url",))["rss_url"]

    items = parse_items(fetch_rss(rss_url))
    print(f"RSS: {rss_url} → {len(items)} 件")

    if CSV_PATH.exists():
        rows, fieldnames = load_rows()
    else:
        rows, fieldnames = [], FIELDNAMES
        print(f"{CSV_PATH.relative_to(PROJECT_ROOT)} が無いので新規作成します")

    before = len(rows)
    added, retitled, dated = merge(rows, items)

    rss_slugs = {item["slug"] for item in items}
    only_local = [row for row in rows if slug_of(row) not in rss_slugs]

    for item in added:
        print(f"追加: {item['title']}（{item['published_at'] or item['slug']}）")
    for old, new in retitled:
        print(f"改題: {old}\n   → {new}")
    for item in dated:
        print(f"日時補完: {item['title']} → {item['published_at']}")
    if only_local:
        print(f"RSSに無い既存行: {len(only_local)} 件（削除しません。フォロワー限定回など）")

    changed = bool(added or retitled or dated)
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


if __name__ == "__main__":
    sys.exit(main())
