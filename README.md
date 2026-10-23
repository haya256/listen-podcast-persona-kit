# Podcast Knowledge Kit

LISTEN で配信されているポッドキャストの発言を整理し、「この人ならなんと答えるか」に**根拠付きで**答えられるようにするための道具一式。

AI（Claude Code）と一緒に使うことを前提にしている。番組を1話ずつ整理していくと、トピック別のスタンスと人物像が溜まっていき、質問に対して「いつ・どの回の・何分何秒の発言か」を示しながら答えられるようになる。

## どんなことができるか

整理が溜まったところで、たとえば「<ホスト>に質問。好きな映像作品は？」と聞くと、次の形で答えが返る。

- **回答**: ホストの話し方をまねた文章。過去の発言から言えないことは推測と明記する
- **根拠**: 使った発言の「タイトル（公開日） mm:ss」とエピソードURL
- **補足**: 発言の時期（今も同じとは限らないもの）、プライバシーに配慮して答えなかったことなど

関係する回がまだ整理されていなければ、エピソード一覧のタイトルとタグから候補の回を挙げ、整理を提案してくる。その回を整理すると、そのまま答えられるようになる。

## 何が入っているか

| | |
|---|---|
| `CLAUDE.md` | AIへの指示。回答の作り方、根拠の示し方、整理のルール |
| `scripts/sync_episodes.py` | RSSからエピソード一覧（`episodes.csv`）を作成・最新化 |
| `scripts/fetch_transcripts.py` | 指定した回の文字起こし（VTT）を取得 |
| `scripts/listen_api.py` | （応用編・任意）LISTEN の API で、RSS に載らない限定回の行と公開日時を `episodes.csv` に反映。限定回・有料回の文字起こしも取得 |
| `scripts/check_knowledge.py` | 整理済みデータ（`episodes.csv` と `knowledge/`）の整合性を検査 |
| `scripts/check_public.py` | 公開前に番組固有の情報の混入を検査 |
| `scripts/podcast_config.py` / `scripts/episodes_csv.py` | 上のスクリプトが共用する部品（設定とパス、CSVの読み書き） |
| `templates/` | 設定と整理データの雛形 |

番組のデータそのものはこのリポジトリに入らない。`config/` `data/` `pmo/` は `.gitignore` 済み。

## 動作環境

- Python 3.9 以上。標準ライブラリだけで動くので、依存パッケージのインストールは不要
- [Claude Code](https://claude.com/claude-code)（整理と回答をAIに任せる部分）

## セットアップ

### 1. クローンする

1つの番組につき1つ、このリポジトリをクローンする。ディレクトリ名は番組に合わせておく（例: LISTEN の番組 URL `https://listen.style/p/<番組slug>` の slug を使って `podcast-<番組slug>`）。複数の番組を扱っても取り違えず、Claude Code の会話履歴やメモリもディレクトリごとに分かれる。

```bash
git clone https://github.com/haya256/listen-podcast-persona-kit.git podcast-<番組slug>
cd podcast-<番組slug>
```

リポジトリが非公開のあいだは、`git clone` の代わりに GitHub CLI でクローンする（`gh auth login` 済みのアカウントで認証される）。

```bash
gh repo clone haya256/listen-podcast-persona-kit podcast-<番組slug>
```

デフォルトの名前（`listen-podcast-persona-kit`）でクローン済みなら、あとから名前を変えればよい。Claude Code を起動する前に変えておく（起動後に変えると、それまでの会話履歴やメモリが新しい名前のディレクトリに引き継がれない）。

```bash
mv listen-podcast-persona-kit podcast-<番組slug>
```

### 2. 番組を設定する

```bash
mkdir -p config && cp templates/podcast.md config/podcast.md
```

`config/podcast.md` の frontmatter を対象の番組に合わせて書き換える。

```yaml
---
name: 番組名
listen_url: https://listen.style/p/<番組slug>
rss_url: https://rss.listen.style/p/<番組slug>/rss
host: ホスト名
---
```

### 3. エピソード一覧を作る

```bash
python3 scripts/sync_episodes.py          # ドライラン
python3 scripts/sync_episodes.py --apply  # 書き込む
```

`data/private/episodes.csv` ができる。新しい回が配信されたら、同じコマンドで最新化できる（タグなど手で書いた列は上書きされない）。

### 4. 整理データの git を用意する

整理データは別の git リポジトリで管理する（理由は「データの置き場所」を参照）。

```bash
cd data/private && git init && cd ../..
```

### 5. Claude Code を起動する

```bash
claude
```

「〇〇の回を整理して」「ホストなら〜になんて答える？」のように話しかける（詳しくは「使い方」）。

## 使い方

### 1話ずつ整理する

整理したい回の文字起こしを取る。公開日時キー・slug・CSVの公開日時表記のどれでも指定できる。

```bash
python3 scripts/fetch_transcripts.py 20260705-073500
```

あとは Claude Code に「この回を整理して」と頼む（タイトルや日付で伝えれば、AIが一覧から回を特定して文字起こしの取得から進める）。`CLAUDE.md` の手順に従って、次のものが作られる・更新される。

- `knowledge/episodes/<キー>.md` — その回の要約、`[mm:ss]` 付きの発言、文字起こしの誤認識メモ
- `knowledge/topics/<トピック>.md` — トピックごとのスタンスの要点と関連エピソード
- `knowledge/persona.md` — 話し方・価値観・プロフィール
- `episodes.csv` のタグ列

**一括取得はしない設計にしている。** 1話ずつ、気になった回から手を付けるのが続けるコツ。

### 整理が済んだら検査する

```bash
python3 scripts/check_knowledge.py --fix
```

整理を重ねると、ファイル同士の食い違いが起きやすくなる。コミットの前にこれを実行し、問題が0件になるまで直す。`--fix` を付けると、トピックの関連エピソード・人物の登場エピソードの並び順（日付の新しい順）だけは自動で直す。検査するのは次の点。

- `episodes.csv` の改行コードが CRLF のままか
- まとめの frontmatter（`key` / `published` / `topics`）が、ファイル名や `episodes.csv` と合っているか
- 整理済みの回の文字起こし列が「あり」か
- トピックのファイルがあり、その関連エピソードにまとめへのリンクがあるか
- 各トピックの関連エピソードが日付の新しい順か
- トピックと人物像のリンク先が存在するか

### 質問する

「<ホスト>に質問。〇〇は？」のように聞く。答え方のルールは `CLAUDE.md` にある。主なものは次のとおり。

- 根拠は必ず発言から取る。発言から直接言えないことは「推測」と書き、どの発言から類推したかを示す
- 関係する発言がなければ、作らずに「まだ話していない（または未整理）」と答える
- 習慣や好みなど時期で変わりうることは、補足に発言時期を書く。時期の違う発言で内容が変わっていれば、新しいほうを優先する
- 住んでいる場所、家族、健康など、身バレや私生活の露出につながる情報は、データにあっても答えない
- フォロワー限定回・有料回の内容は答えに使わない（次の節を参照）

## 限定回・有料回の扱い

フォロワー限定回と有料回（`episodes.csv` の「フォロワー限定」列が `TRUE`、または「有料」列に金額が入っている回）は、文字起こしが手に入れば整理してよい。ただし回答には使わない。

- `fetch_transcripts.py` は公開の文字起こししか取れないので、これらの回はスキップする。番組の持ち主なら、応用編の `listen_api.py transcript` で取れる
- まとめの frontmatter に `access: followers` または `access: paid` を入れる
- 中身はその回のまとめファイルだけに書き、トピックには「回答には使わない」と添えたリンクだけを置く
- 関係する発言があるときは、回答の補足で「限定公開（または有料）の回で話している」とだけ伝え、公開日・タイムスタンプ・まとめファイルのパスを示す。タイトル・URL・中身は書かない

## 応用編: LISTEN API 連携（任意）

ここまでの機能は、LISTEN の API トークンがなくてもすべて使える。番組の持ち主が LISTEN の「API Tokens」設定ページでトークンを発行すると、RSS では取れない情報にも手が届く。

```bash
LISTEN_API_TOKEN=<トークン> python3 scripts/listen_api.py sync           # ドライラン
LISTEN_API_TOKEN=<トークン> python3 scripts/listen_api.py sync --apply   # 書き込む
LISTEN_API_TOKEN=<トークン> python3 scripts/listen_api.py transcript 20260705-073500
```

- `sync`: RSS に載らないフォロワー限定回を `episodes.csv` に追加し、公開日時が空の行を埋める。既存行のフォロワー限定・有料の列は書き換えない（API と食い違えば警告だけ出す）
- `transcript`: 指定した回の文字起こし（VTT）を取得する。指定の仕方は `fetch_transcripts.py` と同じ。番組の持ち主のトークンなら、フォロワー限定回・有料回も取れる
- API は読み取りだけを使う。トークンにはエピソードの更新・削除の権限もあるので、扱いに注意する

## データの置き場所

```
config/podcast.md          対象番組の設定（git管理外）
data/
├── transcripts/           文字起こしVTT（再取得できるので git 管理しない）
└── private/               整理データ。ここを別の git リポジトリにする
    ├── episodes.csv       全話の一覧（タグ列でテーマ別に探せる）
    └── knowledge/
        ├── persona.md     話し方・価値観・プロフィール
        ├── topics/        トピックごとのスタンス
        └── episodes/      回ごとの要約・発言
pmo/                       運用メモ（git管理外）
```

整理データには発言者のプライベートな内容が含まれるので、公開しない前提で扱う。`data/private/` は親の `.gitignore`（`data/`）で除外されているので、公開リポジトリ側に巻き込まれることはない。

`episodes.csv` の「公開日時」（JST、`YYYY-MM-DD HH:MM:SS`）が各回のキーになる。まとめのファイル名はこれを `YYYYMMDD-HHMMSS` にしたもの。話数は参考情報として残しているだけで、管理には使わない。

## 公開前の確認

```bash
python3 scripts/check_public.py
```

`config/podcast.md` から番組名・ホスト名・URL・番組slugを読み取り、git が追跡しているファイルに混入していないか検査する。1件でも見つかれば終了コード1を返す。

## 環境変数

| 変数 | 既定値 | 用途 |
|---|---|---|
| `PODCAST_DATA_DIR` | `./data` | データの置き場所を別の場所にしたいとき |
| `LISTEN_API_TOKEN` | なし | 応用編の `listen_api.py` を使うときだけ必要 |

## 注意

- LISTEN の公式ツールではない。LISTEN の仕様が変わると、RSS や文字起こしの取得が動かなくなることがある
- 文字起こしは LISTEN の自動生成なので、固有名詞の誤認識が多い。まとめの「誤認識メモ」に残していく
- 文字起こしと整理データは、番組の発言をもとにした個人用のデータとして扱う。原文の長い引用は避け、要約と短いフレーズにとどめる
- 自分以外の番組を対象にするときは、配信者の意向に配慮すること

## ライセンス

[MIT License](LICENSE)
