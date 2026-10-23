#!/usr/bin/env python3
"""整理済みデータ（episodes.csv と knowledge/）の整合性を検査する。

使い方:
    python3 scripts/check_knowledge.py
    python3 scripts/check_knowledge.py --fix   # 関連エピソード・登場エピソードを日付順に並べ直してから検査する

検査する内容:
- episodes.csv の改行コードが CRLF のままか
- まとめ（knowledge/episodes/<キー>.md）の frontmatter の key / published がファイル名と合っているか
- まとめに対応する行が episodes.csv にあり、タグ列が frontmatter の topics と一致しているか
- 整理済みの回の文字起こし列が「あり」か（限定回・有料回は除く）
- topics に書いたトピックのファイル（knowledge/topics/<名前>.md）があるか
- そのトピックの「関連エピソード」にまとめへのリンクがあるか
- 各トピックの「関連エピソード」が日付の新しい順か
- 「関連エピソード」「登場エピソード」のリンク行が `- [YYYY-MM-DD](../episodes/<キー>.md)` の形で始まっているか
- people に書いた人物のファイル（knowledge/people/<名前>.md）があり、その「登場エピソード」にまとめへのリンクがあるか
- 各人物の「登場エピソード」が日付の新しい順か
- topics/・people/・persona.md の中のエピソードへのリンク先が存在するか

1件でも問題があれば終了コード 1

--fix では、並び順のずれだけを直す（1行に複数リンクを並べた行はその場に残す）。ほかの問題は直さない
"""

import re
import sys
from pathlib import Path

from episodes_csv import COL_PUBLISHED_AT, COL_TAGS, COL_TRANSCRIPT, key_of, load_rows
from podcast_config import CSV_PATH, KNOWLEDGE_DIR, PROJECT_ROOT, require_csv

EPISODES_DIR = KNOWLEDGE_DIR / "episodes"
TOPICS_DIR = KNOWLEDGE_DIR / "topics"
PEOPLE_DIR = KNOWLEDGE_DIR / "people"
PERSONA_PATH = KNOWLEDGE_DIR / "persona.md"

RELATED_HEADING = "## 関連エピソード"
APPEARANCES_HEADING = "## 登場エピソード"
LINK_RE = re.compile(r"\]\((?:\.\./)?episodes/([0-9]{8}-[0-9]{6})\.md\)")
RELATED_ITEM_RE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\]\(\.\./episodes/([0-9]{8}-[0-9]{6})\.md\)")


def rel(path):
    """表示用のパス。PODCAST_DATA_DIR がプロジェクト外なら絶対パスのまま。"""
    path = Path(path)
    return path.relative_to(PROJECT_ROOT) if path.is_relative_to(PROJECT_ROOT) else path


def read_episode_frontmatter(path):
    """まとめの frontmatter を dict で返す。topics / people は [a, b] 形式をリストにする。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    values = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip()] = value.split("#", 1)[0].strip()
    for list_key in ("topics", "people"):
        items = values.get(list_key, "").strip("[] ")
        values[list_key] = [t.strip() for t in items.split(",") if t.strip()]
    return values


def related_items(path, heading=RELATED_HEADING):
    """トピックの「関連エピソード」節（人物は「登場エピソード」節）のリンク項目を (日付, キー) のリストで返す。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    if heading not in lines:
        return None
    items = []
    for line in lines[lines.index(heading) + 1:]:
        if line.startswith("## "):
            break
        match = RELATED_ITEM_RE.match(line)
        # 1行に複数リンクを並べた行は並び順の検査から外す
        if match and len(LINK_RE.findall(line)) == 1:
            items.append(match.groups())
    return items


def sort_related(path, heading):
    """節のリンク項目を日付の新しい順に並べ直す。並べ直したら True を返す。

    1行1リンクの項目だけが元の位置の枠を入れ替わり、それ以外の行（複数リンクの行など）は動かない。
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    stripped = [line.rstrip("\r\n") for line in lines]
    if heading not in stripped:
        return False
    slots = []
    for i in range(stripped.index(heading) + 1, len(lines)):
        if stripped[i].startswith("## "):
            break
        if RELATED_ITEM_RE.match(stripped[i]) and len(LINK_RE.findall(stripped[i])) == 1:
            slots.append(i)
    items = [lines[i] for i in slots]
    # 同じ日付の項目は元の順を保つ（sorted は安定ソート）
    ordered = sorted(items, key=lambda line: RELATED_ITEM_RE.match(line).group(1), reverse=True)
    if ordered == items:
        return False
    for i, line in zip(slots, ordered):
        lines[i] = line
    path.write_text("".join(lines), encoding="utf-8")
    return True


def fix_order():
    paths = [(p, RELATED_HEADING) for p in sorted(TOPICS_DIR.glob("*.md"))]
    paths += [(p, APPEARANCES_HEADING) for p in sorted(PEOPLE_DIR.glob("*.md"))]
    for path, heading in paths:
        if sort_related(path, heading):
            print(f"並べ直しました: {rel(path)}")


def malformed_items(path, heading):
    """節の中で、エピソードへのリンクがあるのに `- [YYYY-MM-DD](...)` で始まらない行を返す。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    if heading not in lines:
        return []
    bad = []
    for line in lines[lines.index(heading) + 1:]:
        if line.startswith("## "):
            break
        if LINK_RE.search(line) and not RELATED_ITEM_RE.match(line):
            bad.append(line)
    return bad


def check_crlf(problems):
    data = CSV_PATH.read_bytes()
    lf_only = data.count(b"\n") - data.count(b"\r\n")
    if lf_only:
        problems.append(f"{rel(CSV_PATH)}: CRLF ではない改行が {lf_only} 行あります")


def check_episodes(problems):
    rows, _ = load_rows()
    rows_by_key = {key_of(row): row for row in rows}

    for path in sorted(EPISODES_DIR.glob("*.md")):
        key = path.stem
        name = rel(path)
        fm = read_episode_frontmatter(path)
        if fm is None:
            problems.append(f"{name}: frontmatter がありません")
            continue

        if fm.get("key") != key:
            problems.append(f"{name}: frontmatter の key が {fm.get('key')!r} です（ファイル名は {key}）")

        row = rows_by_key.get(key)
        if row is None:
            problems.append(f"{name}: episodes.csv に対応する行がありません")
        else:
            if fm.get("published") != row[COL_PUBLISHED_AT]:
                problems.append(
                    f"{name}: published {fm.get('published')!r} が episodes.csv の {row[COL_PUBLISHED_AT]!r} と違います"
                )
            tags = [t for t in row[COL_TAGS].split(";") if t]
            if tags != fm["topics"]:
                problems.append(f"{name}: タグ列 {';'.join(tags)!r} が topics {';'.join(fm['topics'])!r} と違います")
            if not fm.get("access") and row[COL_TRANSCRIPT] != "あり":
                problems.append(f"{name}: 文字起こし列が {row[COL_TRANSCRIPT]!r} です（「あり」のはず）")

        for label, names, directory, heading in (
            ("トピック", fm["topics"], TOPICS_DIR, RELATED_HEADING),
            ("人物", fm["people"], PEOPLE_DIR, APPEARANCES_HEADING),
        ):
            for item in names:
                item_path = directory / f"{item}.md"
                if not item_path.exists():
                    problems.append(f"{name}: {label} {rel(item_path)} がありません")
                    continue
                items = related_items(item_path, heading) or []
                linked = {k for _, k in items} | set(LINK_RE.findall(item_path.read_text(encoding="utf-8")))
                if key not in linked:
                    problems.append(f"{rel(item_path)}: {key} へのリンクがありません")


def check_topics(problems):
    paths = [(p, RELATED_HEADING) for p in sorted(TOPICS_DIR.glob("*.md"))]
    paths += [(p, APPEARANCES_HEADING) for p in sorted(PEOPLE_DIR.glob("*.md"))]
    for path, heading in paths:
        items = related_items(path, heading)
        name = rel(path)
        if items is None:
            problems.append(f"{name}: 「{heading}」の節がありません")
            continue
        for line in malformed_items(path, heading):
            problems.append(f"{name}: {heading[3:]}の行が `- [YYYY-MM-DD](../episodes/<キー>.md)` の形になっていません: {line}")
        dates = [d for d, _ in items]
        if dates != sorted(dates, reverse=True):
            problems.append(f"{name}: {heading[3:]}が日付の新しい順になっていません")
        for date, key in items:
            if date.replace("-", "") != key[:8]:
                problems.append(f"{name}: {heading[3:]} [{date}] のリンク先が {key} です")


def check_links(problems):
    paths = sorted(TOPICS_DIR.glob("*.md")) + sorted(PEOPLE_DIR.glob("*.md")) + [PERSONA_PATH]
    for path in paths:
        if not path.exists():
            continue
        for key in sorted(set(LINK_RE.findall(path.read_text(encoding="utf-8")))):
            if not (EPISODES_DIR / f"{key}.md").exists():
                problems.append(f"{rel(path)}: リンク先 episodes/{key}.md がありません")


def main():
    require_csv()
    if "--fix" in sys.argv[1:]:
        fix_order()
    problems = []
    check_crlf(problems)
    check_episodes(problems)
    check_topics(problems)
    check_links(problems)

    if problems:
        print(f"{len(problems)} 件の問題があります:")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)
    print("問題は見つかりませんでした。")


if __name__ == "__main__":
    sys.exit(main())
