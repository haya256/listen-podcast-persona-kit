#!/usr/bin/env python3
"""LISTEN のエピソードタイトルを、LISTEN の REST API で変更する（応用編・任意。番組の持ち主向け）。

使い方:
    LISTEN_API_TOKEN=<トークン> python3 scripts/listen_title.py 20260705-073500 "新しいタイトル"          # 試し実行（何も変えない）
    LISTEN_API_TOKEN=<トークン> python3 scripts/listen_title.py 20260705-073500 "新しいタイトル" --apply  # 変更して、episodes.csv も直す

- LISTEN の REST API（https://listen.style/api-docs ）の PATCH /api/v1/episodes/{ID} を使う。LISTEN MCP Server には
  タイトルを変えるツールがないため
  - title は、API ドキュメントの PATCH の項目には載っていない（2026-11 に送って変わることを確かめた）。
    使えなくなったら止まるので、そのときは LISTEN の画面から変える
- 必要なもの: 環境変数 LISTEN_API_TOKEN（update 権限が要る）と、config/podcast.md の podcast_id
- 1回に1話だけ。エピソードは番組のエピソード一覧から slug で探す（新しい順に、見つかるまで100話ずつ）
- 試し実行（既定）は、今のタイトルと新しいタイトルを表示するだけで、変更は送らない（API に試し実行の仕組みがないため）
- --apply のときは、取得したときの revision を expected_revision に付けて送る（途中で他の編集が入っていれば 409 で止まる）。
  変更後にもう一度取得し、タイトルが変わったことを確かめてから episodes.csv を直す
- 次のときは、再試行せずに止める: LISTEN のタイトルが episodes.csv と違う、エピソードが見つからない、エラーが返った
- 指定の仕方は fetch_transcripts.py と同じ（公開日時キー・CSVの公開日時表記・slug）
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from episodes_csv import COL_PUBLISHED_AT, COL_TITLE, load_rows, save_rows, slug_of
from podcast_config import CONFIG_PATH, CSV_PATH, PROJECT_ROOT, USER_AGENT, load_config, require_csv

API_BASE = "https://listen.style/api/v1"
PAGE_SIZE = 100  # 一覧の first の上限
REQUEST_INTERVAL_SEC = 1  # LISTEN の API は利用者ごとに1分60リクエストまで
TOKEN_HINT = (
    "LISTEN_API_TOKEN が設定されていません。LISTEN の「API Tokens」設定ページで update 権限のあるトークンを発行し、"
    "環境変数 LISTEN_API_TOKEN に入れて実行してください。"
)


class TitleError(Exception):
    pass


class Api:
    def __init__(self, token):
        self._token = token
        self._last = 0.0

    def request(self, method, path, body=None):
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            f"{API_BASE}{path}",
            method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise TitleError(f"{method} {API_BASE}{path} が HTTP {e.code} を返しました: {detail}\n再試行はしません")
        except urllib.error.URLError as e:
            raise TitleError(f"{method} {API_BASE}{path} に接続できませんでした: {e.reason}\n再試行はしません")
        finally:
            self._last = time.monotonic()


def find_episode(api, podcast_id, slug):
    """番組のエピソード一覧から slug が一致する回を探す。"""
    after = None
    while True:
        query = {"first": PAGE_SIZE}
        if after:
            query["after"] = after
        page = api.request("GET", f"/podcasts/{podcast_id}/episodes?{urllib.parse.urlencode(query)}")
        for episode in page.get("data") or []:
            if episode.get("slug") == slug:
                return episode
        after = (page.get("meta") or {}).get("next_cursor")
        if not after:
            raise TitleError(f"番組のエピソード一覧に slug「{slug}」の回が見つかりませんでした")


def change_title(rows, fieldnames, key, new_title, apply_changes):
    from fetch_transcripts import find_row

    config = load_config(required=("name",))
    podcast_id = config.get("podcast_id", "").strip()
    if not podcast_id:
        raise TitleError(
            f"{CONFIG_PATH.relative_to(PROJECT_ROOT)} に podcast_id がありません。"
            "`python3 scripts/listen_mcp.py sync` を実行すると、書く値が表示されます"
        )
    row = find_row(rows, key)
    if row is None:
        raise TitleError(f"エピソード「{key}」が episodes.csv に見つかりません")
    old_title, new_title = row[COL_TITLE], new_title.strip()
    if not new_title:
        raise TitleError("新しいタイトルが空です")
    if new_title == old_title:
        raise TitleError("新しいタイトルが今のタイトルと同じです")

    api = Api(os.environ["LISTEN_API_TOKEN"].strip())
    episode = find_episode(api, podcast_id, slug_of(row))
    episode_id = episode["id"]
    current = api.request("GET", f"/episodes/{episode_id}").get("data") or {}
    if current.get("title") != old_title:
        raise TitleError(f"LISTEN のタイトルが episodes.csv と違います（LISTEN: {current.get('title')} / CSV: {old_title}）")

    print(f"対象: {old_title}（{row[COL_PUBLISHED_AT]}）\n  → {new_title}")
    if not apply_changes:
        print("試し実行です（何も変えていません）。変更するには --apply を付けてください")
        return 0

    api.request("PATCH", f"/episodes/{episode_id}", {"title": new_title, "expected_revision": current.get("revision")})
    after = api.request("GET", f"/episodes/{episode_id}").get("data") or {}
    if after.get("title") != new_title:
        raise TitleError(
            f"変更を送りましたが、タイトルが変わっていません（今: {after.get('title')}）。"
            "API が title を受け付けなくなったのかもしれません。LISTEN の画面で確かめてください"
        )
    row[COL_TITLE] = new_title
    save_rows(rows, fieldnames)
    print(f"変更しました。{CSV_PATH.relative_to(PROJECT_ROOT)} のタイトルも直しました")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("key", help="公開日時キー・CSVの公開日時表記・slug（1話だけ）")
    parser.add_argument("title", help="新しいタイトル")
    parser.add_argument("--apply", action="store_true", help="変更する（指定しなければ試し実行）")
    args = parser.parse_args()

    if not os.environ.get("LISTEN_API_TOKEN", "").strip():
        print(TOKEN_HINT, file=sys.stderr)
        return 1
    require_csv()
    rows, fieldnames = load_rows()
    try:
        return change_title(rows, fieldnames, args.key, args.title, args.apply)
    except TitleError as e:
        print(f"エラー: {e}\nここで止めます。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
