#!/usr/bin/env python3
"""RSSから episodes.csv を作成・最新化する。

使い方:
    python3 scripts/sync_episodes.py            # ドライラン（差分を表示するだけ）
    python3 scripts/sync_episodes.py --apply    # 実際に書き込む
    python3 scripts/sync_episodes.py --rss <URL> --apply

- 突き合わせのキーはURL末尾のslug（公開日時は空の回があるためキーに使えない）
- RSSから取るのはタイトル・公開日時・URLだけ
- 手動管理の列（フォロワー限定・文字起こし・エピソード番号）は上書きしない
- エピソード番号: 既存の行の番号が、公開日時の古い順に1からの連番にそろっているときだけ、
  それより新しい追加行に続きの番号を振る（そろっていなければ空のまま）。RSS に出ないフォロワー限定回を
  あとから手で足したときは、その後ろの回の番号を振り直す必要がある
- RSSに無い既存行は削除しない（フォロワー限定回はRSSに出ないため）
"""

import argparse
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
from podcast_config import CSV_PATH, PROJECT_ROOT, USER_AGENT, load_config

JST = timezone(timedelta(hours=9))


def fetch_rss(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as res:
        return res.read()


def parse_items(raw):
    """RSSの各itemを {slug, title, published_at, published_on, url, audio_url} にする。

    audio_url（enclosure の音声URL）は whisper_transcribe.py 用で、CSV には書かない。
    """
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

        enclosure = item.find("enclosure")
        items.append(
            {
                "audio_url": (enclosure.get("url") or "").strip() if enclosure is not None else "",
                "slug": link.rstrip("/").rsplit("/", 1)[-1],
                "title": (item.findtext("title") or "").strip(),
                "published_at": published_at,
                "published_on": published_on,
                "url": link,
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
                    # RSS に文字起こしのタグがなくてもページにはあることがあるので、取りに行くまでは「未取得」
                    COL_TRANSCRIPT: "未取得",
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


def number_new_rows(existing, added_rows):
    """既存の行が 1 からの連番なら、それより新しい追加行に続きの番号を振る。振った行数か、振れない理由を返す。"""
    if not added_rows:
        return 0
    if not existing:
        return "既存の行がないため"
    if any(not row[COL_PUBLISHED_AT] for row in existing + added_rows):
        return "公開日時が空の行があるため"
    ordered = sorted(existing, key=lambda row: row[COL_PUBLISHED_AT])
    if [row[COL_EPISODE_NO] for row in ordered] != [str(i) for i in range(1, len(ordered) + 1)]:
        return "既存の行の番号が1からの連番になっていないため"
    latest = ordered[-1][COL_PUBLISHED_AT]
    if any(row[COL_PUBLISHED_AT] <= latest for row in added_rows):
        return "既存の最新の回より古い回が追加されたため"
    for i, row in enumerate(sorted(added_rows, key=lambda row: row[COL_PUBLISHED_AT]), len(ordered) + 1):
        row[COL_EPISODE_NO] = str(i)
    return len(added_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="実際に書き込む（指定しなければドライラン）")
    parser.add_argument("--rss", metavar="URL", help="RSS の URL（省略時は config/podcast.md の rss_url）")
    args = parser.parse_args()
    apply_changes = args.apply
    rss_url = args.rss
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
    existing = list(rows)
    added, retitled, dated = merge(rows, items)
    added_slugs = {item["slug"] for item in added}
    added_rows = [row for row in rows if slug_of(row) in added_slugs]
    numbered = number_new_rows(existing, added_rows)

    rss_slugs = {item["slug"] for item in items}
    only_local = [row for row in rows if slug_of(row) not in rss_slugs]

    for row in sorted(added_rows, key=lambda row: row[COL_PUBLISHED_AT]):
        number = f"第{row[COL_EPISODE_NO]}話 " if row[COL_EPISODE_NO] else ""
        print(f"追加: {number}{row[COL_TITLE]}（{row[COL_PUBLISHED_AT] or slug_of(row)}）")
    if isinstance(numbered, str):
        print(f"エピソード番号は振りませんでした（{numbered}）")
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
