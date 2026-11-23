#!/usr/bin/env python3
"""LISTEN のエピソードタイトルを、ブラウザ（Playwright）で編集画面から変更する（応用編・任意。番組の持ち主向け）。

使い方:
    python3 scripts/listen_title.py --login                                 # 初回だけ。表示されたブラウザで LISTEN にログイン
    python3 scripts/listen_title.py 20260705-073500 "新しいタイトル"            # 試し実行（入力して止まる。保存しない）
    python3 scripts/listen_title.py 20260705-073500 "新しいタイトル" --apply    # 保存して、episodes.csv も直す

- LISTEN MCP Server にはタイトルを変えるツールがないため、編集画面（<エピソードURL>/edit）を操作する
- 必要なもの: Python の playwright と Chromium（pip install playwright && playwright install chromium）、
  ブラウザを表示できる画面（WSL なら WSLg）
- ログイン状態は、リポジトリの外のプロファイル（~/.local/share/listen-podcast-persona-kit/browser-profile）に残る。
  パスワードと同じように扱う（人に渡さない・リポジトリに入れない）
- 1回に1話だけ。押すボタンは名前がちょうど「保存」のものだけ（「下書き保存する」「公開予約」は押さない）
- 次のときは、再試行せずに止める: ログインしていない、タイトル欄の今の値が episodes.csv と違う、
  ボタンが見つからない、更新の表示が出ない、ページの取得がエラーになった
- 指定の仕方は fetch_transcripts.py と同じ（公開日時キー・CSVの公開日時表記・slug）
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

from episodes_csv import COL_PUBLISHED_AT, COL_TITLE, COL_URL, load_rows, save_rows
from podcast_config import CSV_PATH, PROJECT_ROOT, require_csv

PROFILE_DIR = Path.home() / ".local" / "share" / "listen-podcast-persona-kit" / "browser-profile"
LOGIN_URL = "https://listen.style/"  # ここからログインしてもらう
SAVED_MESSAGE = "エピソードを更新しました"
TIMEOUT_MS = 30_000
PLAYWRIGHT_HINT = (
    "playwright が見つかりません。このスクリプトは応用編・任意です。使う場合は\n"
    "  pip install playwright && playwright install chromium\n"
    "を実行してください。"
)


class TitleError(Exception):
    pass


def open_browser(playwright):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILE_DIR, 0o700)
    return playwright.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False)


def login():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = open_browser(p)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL)
        input("表示されたブラウザで LISTEN にログインしたら、ここで Enter を押してください: ")
        context.close()
    print(f"ログイン状態を保存しました（{PROFILE_DIR}）")
    return 0


def goto(page, url):
    response = page.goto(url, timeout=TIMEOUT_MS)
    status = response.status if response else None
    if status is not None and status >= 400:
        raise TitleError(f"ページの取得に失敗しました（{url} が HTTP {status}）。再試行はしません")


def title_input(page):
    """編集画面のタイトル欄。見つからなければ None。"""
    for locator in (page.get_by_label("タイトル", exact=True), page.get_by_role("textbox", name="タイトル", exact=True)):
        if locator.count() == 1:
            return locator
    return None


def change_title(rows, fieldnames, key, new_title, apply_changes, screenshot):
    from fetch_transcripts import find_row
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    row = find_row(rows, key)
    if row is None:
        raise TitleError(f"エピソード「{key}」が episodes.csv に見つかりません")
    old_title, url = row[COL_TITLE], row[COL_URL].rstrip("/")
    new_title = new_title.strip()
    if not new_title:
        raise TitleError("新しいタイトルが空です")
    if new_title == old_title:
        raise TitleError("新しいタイトルが今のタイトルと同じです")
    print(f"対象: {old_title}（{row[COL_PUBLISHED_AT]}）\n  {url}\n  → {new_title}")

    with sync_playwright() as p:
        context = open_browser(p)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            goto(page, f"{url}/edit")
            if "/login" in page.url:
                raise TitleError("ログインしていません。先に `python3 scripts/listen_title.py --login` を実行してください")

            field = title_input(page)
            if field is None:
                page.screenshot(path=screenshot, full_page=True)
                raise TitleError(
                    "タイトル欄が見つかりませんでした（ログインしていないか、画面の作りが変わったかもしれません。"
                    f"ログインしていなければ --login を実行してください）。画面: {screenshot}"
                )
            current = field.input_value().strip()
            if current != old_title.strip():
                raise TitleError(f"タイトル欄の今の値が episodes.csv と違います（LISTEN: {current} / CSV: {old_title}）")

            field.fill(new_title)
            page.screenshot(path=screenshot, full_page=True)
            print(f"入力しました。画面: {screenshot}")
            if not apply_changes:
                print("試し実行です（保存していません）。保存するには --apply を付けてください")
                return 0

            save = page.get_by_role("button", name="保存", exact=True)
            if save.count() != 1:
                raise TitleError(f"「保存」ボタンを1つに決められませんでした（{save.count()} 個）。保存していません")
            save.click()
            try:
                page.get_by_text(SAVED_MESSAGE).first.wait_for(timeout=TIMEOUT_MS)
            except PlaywrightTimeout:
                page.screenshot(path=screenshot, full_page=True)
                raise TitleError(f"「{SAVED_MESSAGE}」の表示が出ませんでした。LISTEN の画面で確かめてください。画面: {screenshot}")

            goto(page, url)
            if new_title not in page.locator("body").inner_text():
                raise TitleError(f"保存の表示は出ましたが、エピソードページに新しいタイトルが見つかりません。{url} を確かめてください")
        finally:
            context.close()

    row[COL_TITLE] = new_title
    save_rows(rows, fieldnames)
    print(f"保存しました。{CSV_PATH.relative_to(PROJECT_ROOT)} のタイトルも直しました")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("key", nargs="?", help="公開日時キー・CSVの公開日時表記・slug（1話だけ）")
    parser.add_argument("title", nargs="?", help="新しいタイトル")
    parser.add_argument("--apply", action="store_true", help="保存する（指定しなければ試し実行）")
    parser.add_argument("--login", action="store_true", help="ブラウザを開いて LISTEN にログインする（初回だけ）")
    parser.add_argument("--screenshot", help="スクリーンショットの保存先（省略時は一時ディレクトリ）")
    args = parser.parse_args()

    try:
        import playwright  # noqa: F401
    except ImportError:
        print(PLAYWRIGHT_HINT, file=sys.stderr)
        return 1

    if args.login:
        return login()
    if not args.key or not args.title:
        parser.error("キーと新しいタイトルを指定してください（初回は --login）")

    require_csv()
    rows, fieldnames = load_rows()
    screenshot = args.screenshot or str(Path(tempfile.gettempdir()) / "listen_title.png")
    try:
        return change_title(rows, fieldnames, args.key, args.title, args.apply, screenshot)
    except TitleError as e:
        print(f"エラー: {e}\nここで止めます。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
