#!/usr/bin/env python3
"""LISTEN の公式ツール「LISTEN MCP Server」を使って、RSS では取れない情報を扱う（応用編）。

使い方:
    LISTEN_API_TOKEN=<トークン> python3 scripts/listen_mcp.py transcript 20260705-073500  # 文字起こし取得
    LISTEN_API_TOKEN=<トークン> python3 scripts/listen_mcp.py sync                        # ドライラン
    LISTEN_API_TOKEN=<トークン> python3 scripts/listen_mcp.py sync --apply                # 書き込む

- LISTEN MCP Server（https://listen.style/document/mcp_server）を、ヘルプに書かれた版（v1.1.0）で起動し、
  そのツールを呼ぶ。LISTEN の内部 API を直接呼ぶことはしない
- 必要なもの: 環境変数 LISTEN_API_TOKEN（LISTEN の「API Tokens」設定ページで発行。read 権限で足りる）と npx（Node.js）。
  どちらかがなければ案内を出して終わる。なくても他のスクリプトは動く
- 呼ぶのは読み取りのツールだけ。文字起こしの編集などの更新系のツールは呼ばない
- 番組 ID は config/podcast.md の podcast_id を使う。なければトークンの持ち主の番組から番組名で探し、
  見つからなければ番組名で検索する。1件に決まれば使う

transcript:
- 指定の仕方は fetch_transcripts.py と同じ（公開日時キー・CSVの公開日時表記・slug。複数指定可）
- 取得済みでも再取得して上書きし、episodes.csv の「文字起こし」列を更新する
- 番組の持ち主のトークンなら、フォロワー限定回・有料回の文字起こしも取れる
- エピソード ID は、新しい50話の一覧から公開日時で探す。見つからない古い回は、エピソードページを1回だけ取得して探す

sync:
- MCP Server のエピソード一覧は新しい50話だけで、slug・公開範囲・価格を返さない。そのため行の追加はせず、
  次の2つだけを行う
  - 公開日時が空の行を、タイトルが一致する回の公開日時で埋める（--apply のときだけ書き込む）
  - episodes.csv にない回（RSS に載らないフォロワー限定回など）を一覧で表示する
"""

import argparse
import html
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta

from episodes_csv import (
    COL_FOLLOWERS_ONLY,
    COL_PAID,
    COL_PUBLISHED_AT,
    COL_PUBLISHED_ON,
    COL_TITLE,
    COL_TRANSCRIPT,
    COL_URL,
    load_rows,
    save_rows,
    slug_of,
)
from podcast_config import CONFIG_PATH, CSV_PATH, PROJECT_ROOT, TRANSCRIPTS_DIR, load_config, require_csv

# LISTEN のヘルプ（https://listen.style/document/mcp_server）に書かれた起動方法。版を固定する
MCP_VERSION = "1.1.0"
MCP_PACKAGE_URL = (
    "https://github.com/ondinc/listen-mcp-server/releases/download/"
    f"v{MCP_VERSION}/ondinc-listen-mcp-server-{MCP_VERSION}.tgz"
)
MCP_COMMAND = ["npx", "-y", f"--package={MCP_PACKAGE_URL}", "listen-mcp-server"]
GRAPHQL_URL = "https://listen.style/graphql"  # MCP Server の設定値としてヘルプに書かれているもの

# 呼んでよいツール（読み取りだけ）。ここにないツールは呼ばない
READ_ONLY_TOOLS = {"get_my_podcasts", "search_podcasts", "get_podcast_episodes", "get_episode", "get_episode_transcript"}

NO_TRANSCRIPT = "Transcript is not available for this episode."
REQUEST_INTERVAL_SEC = 1  # LISTEN の API は利用者ごとに1分60リクエストまで
TIMEOUT_SEC = 180  # 初回は npx が MCP Server をダウンロードするので長めにする
EPISODE_ID_RE = re.compile(r"\b01[0-9a-hjkmnp-tv-z]{24}\b")  # LISTEN の ID（ULID）

TOKEN_HINT = (
    "LISTEN_API_TOKEN が設定されていません。\n"
    "このスクリプトは応用編で、なくても他の機能は使えます。使う場合は LISTEN の\n"
    "「API Tokens」設定ページでトークンを発行し（read 権限で足ります）、環境変数 LISTEN_API_TOKEN に入れて実行してください。"
)
NPX_HINT = "npx が見つかりません。LISTEN MCP Server を使うには Node.js を入れてください。"


class McpError(Exception):
    pass


def _token():
    return os.environ.get("LISTEN_API_TOKEN", "").strip()


def available():
    return bool(_token()) and shutil.which("npx") is not None


def unavailable_reason():
    if not _token():
        return "LISTEN_API_TOKEN が未設定"
    if shutil.which("npx") is None:
        return "npx がない"
    return ""


class McpClient:
    """LISTEN MCP Server を子プロセスで起動し、stdio の JSON-RPC でツールを呼ぶ。"""

    def __enter__(self):
        env = dict(os.environ, LISTEN_API_TOKEN=_token(), LISTEN_GRAPHQL_URL=GRAPHQL_URL)
        self._log = tempfile.TemporaryFile(mode="w+b")
        self._proc = subprocess.Popen(
            MCP_COMMAND,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            env=env,
        )
        self._next_id = 0
        self._last_call = 0.0
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "listen-podcast-persona-kit", "version": "1.0"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *exc):
        if self._proc.poll() is None:
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._log.close()
        return False

    def _send(self, message):
        self._proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self._proc.stdin.flush()

    def _server_log_tail(self):
        self._log.seek(0)
        lines = self._log.read().decode("utf-8", "replace").strip().splitlines()
        return "\n".join(lines[-5:])

    def _request(self, method, params):
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + TIMEOUT_SEC
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpError(f"LISTEN MCP Server から {TIMEOUT_SEC} 秒以内に応答がありませんでした（{method}）")
            ready, _, _ = select.select([self._proc.stdout], [], [], remaining)
            if not ready:
                continue
            line = self._proc.stdout.readline()
            if not line:
                raise McpError(f"LISTEN MCP Server が終了しました。ログの末尾:\n{self._server_log_tail()}")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != request_id:
                continue  # 通知など、この要求への応答ではないもの
            if "error" in message:
                raise McpError(f"{method} がエラーを返しました: {message['error'].get('message', message['error'])}")
            return message.get("result", {})

    def call(self, tool, arguments):
        """ツールを呼び、返ってきたテキストを返す。"""
        if tool not in READ_ONLY_TOOLS:
            raise McpError(f"読み取り以外のツールは呼びません: {tool}")
        wait = REQUEST_INTERVAL_SEC - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        result = self._request("tools/call", {"name": tool, "arguments": arguments})
        self._last_call = time.monotonic()
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise McpError(f"{tool} がエラーを返しました: {text}")
        return text


def normalize_title(title):
    """突き合わせ用。前後の空白を除き、波ダッシュ（〜）と全角チルダ（～）を同一視する。"""
    return (title or "").strip().replace("〜", "～")


def to_jst(pub_date):
    """MCP Server の pubDate（UTC の "YYYY-MM-DD HH:MM:SS"）を JST にする。空なら ("", "")。"""
    if not pub_date:
        return "", ""
    jst = datetime.strptime(pub_date[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S") + timedelta(hours=9)
    return jst.strftime("%Y-%m-%d %H:%M:%S"), jst.strftime("%Y-%m-%d")


def find_podcast_id(client, config):
    podcast_id = config.get("podcast_id", "").strip()
    if podcast_id:
        return podcast_id
    name = config["name"]

    def matches(tool, arguments):
        return [
            p for p in json.loads(client.call(tool, arguments) or "[]")
            if normalize_title(p.get("title")) == normalize_title(name)
        ]

    # まずトークンの持ち主の番組から探す（検索は表記の違い（〜と～）で引っかからないことがあるため）。
    # 持ち主の番組でなければ、番組名で検索する
    hits = matches("get_my_podcasts", {})
    if not hits:
        hits = matches("search_podcasts", {"query": name})
    if len(hits) != 1:
        raise McpError(
            f"番組「{name}」を自分の番組と検索から1つに決められませんでした（該当 {len(hits)} 件）。"
            f"{CONFIG_PATH.relative_to(PROJECT_ROOT)} の frontmatter に podcast_id を書いてください"
        )
    podcast_id = hits[0]["id"]
    print(f"番組 ID: {podcast_id}（{CONFIG_PATH.relative_to(PROJECT_ROOT)} に `podcast_id: {podcast_id}` を書くと、次からは検索しません）")
    return podcast_id


def recent_episodes(client, podcast_id):
    """新しい50話の [{id, title, pubDate, ...}]。"""
    data = json.loads(client.call("get_podcast_episodes", {"podcastId": podcast_id}) or "{}")
    return (data.get("episodes") or {}).get("data") or []


def episode_id_from_page(row, podcast_id):
    """エピソードページを1回だけ取得し、いちばん多く出てくる ID を返す（番組 ID は除く）。"""
    from fetch_transcripts import STOP_HINT, fetch_url

    status, body = fetch_url(row[COL_URL])
    if status != 200 or not body:
        raise McpError(f"エピソード ID を調べるためのページ取得に失敗しました（{row[COL_URL]} が HTTP {status}）\n{STOP_HINT}")
    counts = Counter(i for i in EPISODE_ID_RE.findall(body.decode("utf-8", "replace")) if i != podcast_id)
    if not counts:
        raise McpError(f"ページからエピソード ID を見つけられませんでした（{row[COL_URL]}）")
    return counts.most_common(1)[0][0]


def check_episode(client, episode_id, podcast_id, row):
    """ページから調べた ID が、この番組のこの回か確かめる。"""
    ep = json.loads(client.call("get_episode", {"episodeId": episode_id}) or "null") or {}
    same_podcast = (ep.get("podcast") or {}).get("id") == podcast_id
    published_at, _ = to_jst(ep.get("pubDate"))
    same_episode = (row[COL_PUBLISHED_AT] and published_at == row[COL_PUBLISHED_AT]) or (
        normalize_title(ep.get("title")) == normalize_title(row[COL_TITLE])
    )
    if not (same_podcast and same_episode):
        raise McpError(f"ページから調べた ID（{episode_id}）が、この回と一致しませんでした")


def find_episode_id(client, podcast_id, recent, row):
    if row[COL_PUBLISHED_AT]:
        hits = [ep for ep in recent if to_jst(ep.get("pubDate"))[0] == row[COL_PUBLISHED_AT]]
        if len(hits) > 1:
            hits = [ep for ep in hits if normalize_title(ep.get("title")) == normalize_title(row[COL_TITLE])]
        if len(hits) == 1:
            return hits[0]["id"]
    episode_id = episode_id_from_page(row, podcast_id)
    check_episode(client, episode_id, podcast_id, row)
    return episode_id


def fetch_transcripts(rows, fieldnames, keys):
    """指定された回の文字起こしを MCP Server で取得して保存する。終了コードを返す。"""
    from fetch_transcripts import find_row

    config = load_config(required=("name", "listen_url"))
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    fetched, missing, unavailable, errors = [], [], [], []

    try:
        with McpClient() as client:
            podcast_id = find_podcast_id(client, config)
            recent = recent_episodes(client, podcast_id)
            for key in keys:
                row = find_row(rows, key)
                if row is None:
                    print(f"エラー: エピソード「{key}」が episodes.csv に見つかりません")
                    errors.append(key)
                    continue
                label = f"{row[COL_TITLE]}（{row[COL_PUBLISHED_AT] or slug_of(row)}）"
                limited = row[COL_FOLLOWERS_ONLY] == "TRUE" or bool(row.get(COL_PAID))

                episode_id = find_episode_id(client, podcast_id, recent, row)
                text = client.call("get_episode_transcript", {"episodeId": episode_id, "format": "vtt"})
                if text.strip() == NO_TRANSCRIPT or not text.strip():
                    if limited:
                        # 持ち主でないと取れないだけかもしれないので、状態は変えない
                        print(f"取れず: {label} は限定回・有料回で、このトークンでは文字起こしを取れませんでした（状態は変更しません）")
                        unavailable.append(key)
                    else:
                        row[COL_TRANSCRIPT] = "なし"
                        print(f"なし: {label} は文字起こしが存在しません")
                        missing.append(key)
                    continue
                if not text.lstrip().startswith("WEBVTT"):
                    raise McpError(f"{label} の文字起こしが VTT の形ではありませんでした")

                body = html.unescape(text)  # MCP Server は & を &amp; のまま返すため
                out_path = TRANSCRIPTS_DIR / f"{slug_of(row)}.vtt"
                out_path.write_text(body, encoding="utf-8")
                row[COL_TRANSCRIPT] = "あり"
                print(f"取得（MCP）: {label} → {out_path.relative_to(PROJECT_ROOT)} ({len(body.encode())} bytes)")
                fetched.append(key)
    except McpError as e:
        print(f"エラー: {e}\nここで止めます。")
        errors.append("mcp")

    if fetched or missing:
        save_rows(rows, fieldnames)

    print(
        f"\n結果: 取得 {len(fetched)} / なし {len(missing)} / 取れず {len(unavailable)} / エラー {len(errors)}"
    )
    return 1 if errors else 0


def sync(apply_changes):
    config = load_config(required=("name", "listen_url"))
    require_csv()
    rows, fieldnames = load_rows()

    try:
        with McpClient() as client:
            episodes = recent_episodes(client, find_podcast_id(client, config))
    except McpError as e:
        print(f"エラー: {e}\nここで止めます。")
        return 1
    print(f"MCP: 新しい {len(episodes)} 話（MCP Server が返すのは新しい50話まで）")

    known_dates = {row[COL_PUBLISHED_AT] for row in rows if row[COL_PUBLISHED_AT]}
    undated = {normalize_title(row[COL_TITLE]): row for row in rows if not row[COL_PUBLISHED_AT]}
    dated, not_in_csv = [], []
    for ep in episodes:
        if not ep.get("title") and not ep.get("pubDate"):
            continue  # RSS の不正データと同じ、実体のない項目
        published_at, published_on = to_jst(ep.get("pubDate"))
        if published_at in known_dates:
            continue
        row = undated.pop(normalize_title(ep.get("title")), None)
        if row is not None and published_at:
            row[COL_PUBLISHED_AT], row[COL_PUBLISHED_ON] = published_at, published_on
            dated.append(row)
        else:
            not_in_csv.append(f"{ep.get('title', '').strip()}（{published_at}）")

    for row in dated:
        print(f"日時補完: {row[COL_TITLE]} → {row[COL_PUBLISHED_AT]}")
    for line in not_in_csv:
        print(f"episodes.csv にない回: {line}")
    if not_in_csv:
        print(
            "  → RSS に載らないフォロワー限定回などの可能性があります。MCP Server からは slug・公開範囲・価格が"
            "取れないので、行は追加しません。必要なら手で追加してください"
        )

    if not dated:
        print("書き込む変更はありません")
        return 0
    if not apply_changes:
        print("\nドライランです。書き込むには --apply を付けてください")
        return 0
    save_rows(rows, fieldnames)
    print(f"\n{CSV_PATH.relative_to(PROJECT_ROOT)} を更新しました")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command")
    transcript_parser = commands.add_parser("transcript", help="文字起こしVTTを取得する")
    transcript_parser.add_argument("keys", nargs="+", help="公開日時キー・CSVの公開日時表記・slug")
    sync_parser = commands.add_parser("sync", help="公開日時の補完と、episodes.csv にない回の表示")
    sync_parser.add_argument("--apply", action="store_true", help="実際に書き込む（指定しなければドライラン）")
    args = parser.parse_args()
    if not args.command:
        print(__doc__.strip())
        return 0

    if not _token():
        print(TOKEN_HINT, file=sys.stderr)
        return 1
    if shutil.which("npx") is None:
        print(NPX_HINT, file=sys.stderr)
        return 1

    if args.command == "transcript":
        require_csv()
        rows, fieldnames = load_rows()
        return fetch_transcripts(rows, fieldnames, args.keys)
    return sync(args.apply)


if __name__ == "__main__":
    sys.exit(main())
