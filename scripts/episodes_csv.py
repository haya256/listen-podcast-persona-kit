#!/usr/bin/env python3
"""episodes.csv の列定義と読み書き。"""

import csv

from podcast_config import CSV_PATH

COL_PUBLISHED_AT = "公開日時"
COL_TITLE = "タイトル"
COL_PUBLISHED_ON = "公開日"
COL_FOLLOWERS_ONLY = "フォロワー限定"
COL_PAID = "有料"
COL_TRANSCRIPT = "文字起こし"
COL_URL = "URL"
COL_TAGS = "タグ"
COL_EPISODE_NO = "エピソード番号"

FIELDNAMES = [
    COL_PUBLISHED_AT,
    COL_TITLE,
    COL_PUBLISHED_ON,
    COL_FOLLOWERS_ONLY,
    COL_PAID,
    COL_TRANSCRIPT,
    COL_URL,
    COL_TAGS,
    COL_EPISODE_NO,
]

# RSSから復元できない手動管理の列。sync では絶対に上書きしない。
MANUAL_COLUMNS = (COL_FOLLOWERS_ONLY, COL_PAID, COL_TRANSCRIPT, COL_TAGS, COL_EPISODE_NO)


def load_rows(path=CSV_PATH):
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader), reader.fieldnames


def save_rows(rows, fieldnames, path=CSV_PATH):
    """tmpに書いてから置き換える（書き込み途中で壊れないように）。"""
    tmp_path = path.with_suffix(".csv.tmp")
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(path)


def slug_of(row):
    return row[COL_URL].rstrip("/").rsplit("/", 1)[-1]


def key_of(row):
    """公開日時 "YYYY-MM-DD HH:MM:SS" → "YYYYMMDD-HHMMSS"。公開日時が空なら空文字。"""
    return row[COL_PUBLISHED_AT].replace("-", "").replace(":", "").replace(" ", "-")
