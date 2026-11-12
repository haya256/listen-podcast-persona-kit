#!/usr/bin/env python3
"""LISTEN に文字起こしがない回を、音声から Whisper で文字起こしする（応用編・任意）。

使い方:
    python3 scripts/whisper_transcribe.py 20260705-073500           # 公開日時キーで指定
    python3 scripts/whisper_transcribe.py bbosrqmx                  # URL末尾のslugでも可
    python3 scripts/whisper_transcribe.py 20260705-073500 --model large-v3

- 前提: whisper コマンド（openai-whisper）と ffmpeg が使えること。なければ案内を出して終了する
- 指定の仕方は fetch_transcripts.py と同じ（公開日時キー・CSVの公開日時表記・slug。複数指定可）
- 音声は RSS の enclosure から一時ディレクトリにダウンロードし、終わったら消す
- フォロワー限定回・有料回は RSS に音声がないのでスキップする
- モデルは --model > 環境変数 WHISPER_MODEL > 既定の turbo
- 結果は data/transcripts/<slug>.vtt に保存し（取得済みでも上書き）、episodes.csv の「文字起こし」列を「あり」にする
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from episodes_csv import (
    COL_FOLLOWERS_ONLY,
    COL_PAID,
    COL_PUBLISHED_AT,
    COL_TITLE,
    COL_TRANSCRIPT,
    load_rows,
    save_rows,
    slug_of,
)
from fetch_transcripts import find_row
from podcast_config import PROJECT_ROOT, TRANSCRIPTS_DIR, load_config, require_csv
from sync_episodes import fetch_rss, parse_items

DEFAULT_MODEL = "turbo"
USER_AGENT = "podcast-knowledge-kit"

INSTALL_HINT = (
    "whisper コマンドが見つかりません。Whisper での文字起こしは任意の機能です。\n"
    "使うときは README.md の「応用編: Whisper で文字起こし（任意）」の手順で入れてください。"
)


def whisper_available():
    return shutil.which("whisper") is not None


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as res, open(path, "wb") as f:
        shutil.copyfileobj(res, f)


def transcribe(audio_path, out_dir, model, prompt):
    """whisper を実行し、できた VTT のパスを返す。失敗したら None。"""
    cmd = [
        "whisper", str(audio_path),
        "--language", "ja",
        "--model", model,
        "--output_format", "vtt",
        "--output_dir", str(out_dir),
        # 日本語で同じ文が延々と繰り返される暴走を抑える
        "--condition_on_previous_text", "False",
        # 番組名・ホスト名を正しく表記させる
        "--initial_prompt", prompt,
    ]
    result = subprocess.run(cmd)
    vtt_path = Path(out_dir) / f"{Path(audio_path).stem}.vtt"
    if result.returncode != 0 or not vtt_path.exists():
        return None
    return vtt_path


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("keys", nargs="*", help="公開日時キー・CSVの公開日時表記・slug")
    parser.add_argument("--model", default=os.environ.get("WHISPER_MODEL") or DEFAULT_MODEL)
    args = parser.parse_args()
    if not args.keys:
        print(__doc__.strip())
        return 0

    if not whisper_available():
        print(INSTALL_HINT, file=sys.stderr)
        return 1

    config = load_config(required=("rss_url",))
    require_csv()
    rows, fieldnames = load_rows()
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    audio_urls = {item["slug"]: item["audio_url"] for item in parse_items(fetch_rss(config["rss_url"]))}
    prompt = "。".join(
        s for s in (config.get("name"), f"ホストは{config['host']}です" if config.get("host") else "") if s
    ) + "。"

    done, skipped, errors = [], [], []
    for key in args.keys:
        row = find_row(rows, key)
        if row is None:
            print(f"エラー: エピソード「{key}」が episodes.csv に見つかりません")
            errors.append(key)
            continue

        label = f"{row[COL_TITLE]}（{row[COL_PUBLISHED_AT] or slug_of(row)}）"
        if row[COL_FOLLOWERS_ONLY] == "TRUE" or row.get(COL_PAID):
            print(f"スキップ: {label} はフォロワー限定回・有料回のため、RSS に音声がありません")
            skipped.append(key)
            continue

        audio_url = audio_urls.get(slug_of(row))
        if not audio_url:
            print(f"エラー: {label} の音声URLが RSS に見つかりません")
            errors.append(key)
            continue

        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / f"{slug_of(row)}.mp3"
            print(f"音声をダウンロード中: {label}")
            download(audio_url, audio_path)
            print(f"文字起こし中（モデル: {args.model}）: {label}")
            vtt_path = transcribe(audio_path, tmp, args.model, prompt)
            if vtt_path is None:
                print(f"エラー: {label} の文字起こしに失敗しました")
                errors.append(key)
                continue
            out_path = TRANSCRIPTS_DIR / f"{slug_of(row)}.vtt"
            shutil.move(str(vtt_path), out_path)

        row[COL_TRANSCRIPT] = "あり"
        save_rows(rows, fieldnames)  # 1話ごとに保存（長時間処理の途中で止まっても結果を残す）
        print(f"完了: {label} → {out_path.relative_to(PROJECT_ROOT)} ({out_path.stat().st_size} bytes)")
        done.append(key)

    print(f"\n結果: 文字起こし {len(done)} / スキップ {len(skipped)} / エラー {len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
