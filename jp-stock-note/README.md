# 日本株 分析ノート（個人用）

日本株初心者の本人1名が使う、**日次終値ベース**の分析Webアプリ。1つの銘柄について「チャート・財務・市場環境」の3点を、根拠となる事実とともに自分で説明できるようになることを目的にしている。
アプリは事実を整理して示すだけで、判断はしない（売買の提案・総合評価・将来の株価水準の提示はしない）。

- 仕様：`../files/requirements-fable.md`（フェーズ0〜4をすべて実装済み）
- 個人利用・非公開。サーバーは **`127.0.0.1` にだけバインド**し、ループバック以外からの接続と、`127.0.0.1`／`localhost` 以外の Host ヘッダーを拒否する。
- J-Quants Light は個人利用限定のライセンスのため、取得データ（`data/`）やエクスポートしたファイル、画面のスクリーンショットを共有・公開しない。

## 必要なもの

- Python 3.12（Windows は python.org 版。`py` ランチャーまたは `python` が使えること）
- Node.js 20 以上と npm（フロントエンドのビルド用。v24 で確認済み）

## 起動（1コマンド）

### Windows

```bat
start.bat
```

エクスプローラーから `start.bat` をダブルクリックしてもよい。ポートを変える場合は、コマンドプロンプトで：

```bat
set PORT=8001
start.bat
```

### Linux / macOS

```bash
./start.sh
PORT=8001 ./start.sh   # ポートを変える場合
```

### 起動時に行うこと（両スクリプト共通）

1. `.venv` が無ければ作成し、`backend/requirements.txt` をインストール（Windows は `py -3`、無ければ `python` を使用）
2. `frontend/dist` が無いか、`frontend/src` が新しければフロントエンドをビルド（`node_modules` が無ければ `npm install`）
3. DBマイグレーション（`data/app.db`、WALモード）
4. <http://127.0.0.1:8000> で起動（`PORT` で変更可、バインド先は常に `127.0.0.1`）

起動中は、起動時の追いかけ取得（PCを止めていた日の分の補完）と、毎日20:00 JSTの夜間バッチがバックグラウンドで動く。

## .env の設定（APIキー）

```bash
cp .env.example .env        # Windows: copy .env.example .env
# .env を開き、JQUANTS_API_KEY= の後に J-Quants の APIキーを書く
```

| 変数 | 内容 |
|---|---|
| `JQUANTS_API_KEY` | J-Quants v2 の APIキー。サーバーの取得層だけが読み、`x-api-key` ヘッダーで送る |
| `STOCKAPP_DB_PATH` | （任意）SQLite の場所。既定は `data/app.db` |

- キーはログ・DB・APIレスポンス・フロントのビルド物のどこにも出さない（テストで確認）。`/api/status` は「設定済みかどうか」の真偽値だけを返す。
- `.env` はコミットしない（`.gitignore` 済み）。
- キーが未設定でもアプリは起動する。その場合、取得は通信せずに「APIキー未設定」として取得履歴に記録される。
- FRED（米金利・ドル円・日本の金利）はキー不要。サーバーだけが取得し、ブラウザは外部へ通信しない。

## 画面

| 画面 | URL | 内容 |
|---|---|---|
| S0 初回説明 | （初回は全URLでこれだけを表示）／再表示 `/about` | 日次終値ベースである理由、アプリがしないこと、拡張版免責3点（①情報提供目的 ②データの非保証と制約 ③最終判断は自己責任）、データの取り扱い。「上記を読み、内容を理解しました」に印を付けて確認するまで、ほかの画面とメニューは表示されない。確認はサーバー（`app_settings`）に記録され、`about.json` の version を上げると再確認になる |
| S1 ホーム | `/` | 銘柄検索、**大引け後ダイジェスト**（見直し日が到来した判断メモ、ウォッチリスト各銘柄の前営業日からの事実の変化＝新たに保存された開示・新たに該当した確認ポイント・自銘柄の変動幅を超える値動き）、マクロ帯 |
| S2 検索結果 | `/search?q=…` | コード・銘柄名の部分一致。市場区分・17業種・33業種を併記 |
| S3 銘柄ページ | `/stocks/72030` | ヘッダー、ウォッチリスト追加／解除、確認ポイント帯、事実要約カード4枚、チャート＋テクニカル4段階、クロス発生履歴、財務・バリュエーション、マクロ・業種感応、**判断メモ**（この銘柄のメモ一覧と作成フォーム） |
| S4 判断メモ | `/memos`、`/memos/{id}` | 全メモの一覧（見直し日の到来を表示）、詳細（リビジョンの内容、**保存時スナップショットと現在値の並記**、振り返りの追記、新しいリビジョンとしての書き直し）、エクスポート |
| ウォッチリスト | `/watchlist` | 銘柄・監視理由タグと理由文・追加日・基準日終値・確認ポイント該当数・判断メモ数と見直し日到来。並び順は追加日順／コード順／タグ別のみ。追加・解除 |
| S5 データ状態・設定 | `/status` | 取得履歴、系列ごとの最終取得と基準日、手動取得、短観の想定為替レート設定、判断メモの改変検知（SHA-256照合）、エクスポート、バックアップの説明、初回説明の再表示リンク |

全画面のフッターに常設版免責（要件 3.11）を表示する。PC幅とスマートフォン幅（390px で確認）の両方で、横にはみ出さずに表示される（幅の広い表は表の中だけ横スクロール）。

### 判断メモ（要件 3.10）

- 入力：区分（監視／保有／見送り。自分で選ぶ。初期選択なし）、根拠チェックリスト（チャート・財務・市場環境の**3分野それぞれ1項目以上**、各項目に所見）、想定シナリオ（想定どおりの場合／想定と異なる場合）、見直し条件（**1件以上**、種類つき）、見直し日（任意）。欠けていると保存できない（画面とサーバーの両方で検査）。
- 売買の入力欄、損益、予想の当たり外れの表示はない。フォームに「このメモは自分の判断過程の記録であり、アプリは内容を評価しません。」と表示する。
- 保存時に自動スナップショット：終値・予想PER・自己資本比率・TOPIX・ドル円の5項目を、**各値の基準日つき**で記録（算出できない場合はその理由を記録）。ほかにテクニカル・バリュエーション・マクロの値と、カード・段階表示の事実文も記録する。
- **保存後は編集・削除できない**：`memo`／`memo_revision`／`memo_snapshot`／`memo_review` の UPDATE・DELETE は SQLite トリガーで拒否される。書き直しは同じメモの新しいリビジョンとして追記され、各行の SHA-256 で改変を検知する。ローカルのDBファイルを直接書き換える操作そのものは防げない（S5 に明記）。
- 見直し日が到来し、その日以降に振り返りの追記がないメモは S1 に表示される。

### ウォッチリスト（要件 3.9）

- 追加時は**監視理由タグを1つ以上**（「決算待ち」「業績の方向確認」「金利感応の観察」「為替感応の観察」「値動きの推移確認」、合わなければ「自由入力」）選び、監視理由の文（必須・200文字以内）を書く。理由が空では追加できない（サーバーとDBトリガーでも拒否）。
- 上限30銘柄（暫定）。解除は削除ではなく解除日の記録（履歴はエクスポートに残る）。
- 毎週土曜の夜間バッチで、ウォッチリスト銘柄の決算サマリーをコード指定で再取得し、訂正を検知する（レート制限キューの優先度P3）。

## データの取得

S5 のボタン、またはコマンドラインで取得する（どちらも同じレート制限キューを通る）。

```bash
cd backend
../.venv/bin/python -m stockapp.ingest master      # 銘柄マスタ  /v2/equities/master
../.venv/bin/python -m stockapp.ingest calendar    # 営業日カレンダー /v2/markets/calendar
```

Windows では `..\.venv\Scripts\python.exe -m stockapp.ingest master` のように読み替える。

- 日足・TOPIX・決算サマリーは、銘柄ページを開いたときのオンデマンド取得（P1）と夜間バッチ（P2）で取得する。
- 送出はトークンバケットで **50回/分**（任意の60秒間で50回以下）。429・5xx・タイムアウト・接続エラーは指数バックオフで最大3回再試行。403 は再試行せず「プラン制限」として記録する。
- 1リクエストごとの送出時刻は `api_request_log` テーブルに残る。

## バックアップとエクスポート

- **DBの世代バックアップ**：夜間バッチの最後に `data/backup/app-YYYYMMDD-HHMMSS.db` へ SQLite のオンラインバックアップでコピーし、**7世代**を残す。手動実行：

  ```bash
  cd backend && ../.venv/bin/python -m stockapp.backup
  ```

  復元はアプリを止めてから、バックアップファイルを `data/app.db` に上書きコピーする（`app.db-wal`／`app.db-shm` があれば一緒に退避する）。
- **エクスポート**（判断メモの全リビジョン・スナップショット・振り返り、ウォッチリストの履歴、改変検知の結果）：S4 一覧または S5 のボタン、もしくは
  - JSON：<http://127.0.0.1:8000/api/export?format=json>
  - Markdown：<http://127.0.0.1:8000/api/export?format=md>

  スナップショットの数値は J-Quants 等の取得データにもとづくため、エクスポートしたファイルも共有・公開しない。

## API

ブラウザは同一オリジンの `/api/*` だけを呼ぶ。外部API（J-Quants・FRED）へはサーバーだけが通信する。

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/search?q=` | 銘柄検索（全角半角・大文字小文字を正規化、部分一致、最大50件） |
| GET | `/api/stocks/{code}/header` | 銘柄ヘッダー（`state` は `ok` か `not_fetched`。4桁・5桁どちらのコードも可） |
| POST | `/api/stocks/{code}/ensure-history` | 日足・TOPIXのオンデマンド取得（保存済みかつ最新ならAPIを呼ばない） |
| GET | `/api/stocks/{code}/chart?range=3m\|6m\|1y\|3y\|5y` | 調整後ローソク足・出来高・移動平均4本・決算開示日／ストップ高・安マーカー |
| GET | `/api/stocks/{code}/technicals?range=…` | テクニカル4段階の事実文・系列、クロス発生履歴（上昇・下落の全件併記） |
| GET | `/api/stocks/{code}/cards/price-position` | 価格位置カード（最大4文・各文に基準日） |
| POST | `/api/stocks/{code}/ensure-fins?force=` | 決算サマリーのオンデマンド取得（`force=true` で再取得＝訂正検知） |
| GET | `/api/stocks/{code}/financials` | 通期推移・四半期単独値・進捗率・予想修正履歴・簡易財務・CF・確認ポイント・訂正検知履歴 |
| GET | `/api/stocks/{code}/valuation` | 予想PER・PBR（推定）・予想配当利回りの日次系列と自社過去レンジ内の位置、ROE・配当性向 |
| GET | `/api/stocks/{code}/cards/business` | 業績方向・財務健全性・バリュエーション・マクロ位置カード |
| GET | `/api/stocks/{code}/macro` | 業種感応タグ・確認表・マクロ系列（銘柄の基準日に合わせる） |
| GET | `/api/macro` | マクロ帯（S1） |
| GET / POST | `/api/settings/tankan` | 日銀短観の想定為替レート（値・調査回・入力日） |
| GET | `/api/glossary/{term}?code=` | 用語の3段解説（定義／実数値を代入した式／誤解しやすい点） |
| GET | `/api/status` | 取得履歴・系列ごとの最終成功・直近60秒の送出数 |
| POST | `/api/fetch/master`, `/api/fetch/calendar` | 銘柄マスタ・営業日カレンダーの手動取得 |
| POST | `/api/fetch-fred?series=` | FRED の手動取得（全系列または1系列） |
| GET | `/api/memo-form` | 判断メモの入力フォーム定義（選択肢のみ。初期選択・提案なし） |
| GET / POST | `/api/stocks/{code}/memos` | 銘柄別の判断メモ一覧／作成（スナップショットを自動記録） |
| GET | `/api/memos?limit=` | 全判断メモの一覧（見直し日到来フラグつき） |
| GET | `/api/memos/{id}` | メモ詳細（全リビジョン・スナップショット・振り返り・改変検知） |
| POST | `/api/memos/{id}/revisions` | 新しいリビジョンの追記 |
| GET | `/api/memos/{id}/review?revision_id=` | スナップショットと現在値の並記（同じ項目・各基準日・差） |
| POST | `/api/memos/{id}/reviews` | 振り返り（プロセス×結果の自己記録）の追記 |
| GET | `/api/memos/verify` | 全メモの SHA-256 照合 |
| GET | `/api/export?format=json\|md` | エクスポート |
| GET / POST | `/api/watchlist?sort=added\|code\|tag` | ウォッチリストの一覧／追加（`{code, reason, tags}`） |
| GET / DELETE | `/api/watchlist/{code}` | 1銘柄の状態／解除（解除日を記録） |
| GET | `/api/digest` | S1 大引け後ダイジェスト |
| GET | `/api/about` | S0 の文面と確認状態 |
| GET / POST | `/api/settings/first-run` | S0 の確認状態／確認の記録 |

## テスト

```bash
./run_tests.sh
```

venv の用意 → バックエンド依存のインストール → フロントエンドのビルド（型検査つき）→ pytest 全件 → フェーズ3（マクロ）テストの再掲 → ビルド物の grep（`api.jquants.com`・`JQUANTS_API_KEY`・`fred.stlouisfed.org`・`fredgraph`・`x-api-key` が含まれないこと）の順に実行する。
テストはすべてフィクスチャと偽トランスポートで動き、J-Quants・FREDには通信しない。禁止語テスト（`tests/test_banned_words.py`）は、`content/` の全ファイル（文言テンプレート・用語解説・判断メモのフォーム定義・S0文面ほか）、サーバーが生成する事実文（カード・段階表示・ダイジェスト・メモの比較・エクスポート）、`frontend/src` の全ソースを検査する。
`run_tests.sh` は bash 用。Windows では Git Bash か WSL で実行するか、`cd backend` → `..\.venv\Scripts\python.exe -m pytest -q` で pytest だけを実行する。

## リモートから使う場合（Tailscale Serve）の注意

- 既定どおり、**`127.0.0.1` バインドのまま**使う。`0.0.0.0` やLANのアドレスにバインドを変えない。
- 外出先のスマートフォンなどから使いたい場合に検討できるのは、**自分の tailnet の中だけに公開する Tailscale Serve** に限る。
- **Tailscale Funnel は絶対に使わない。** Funnel はインターネット全体に公開する機能で、J-Quants の個人利用ライセンスに反し、認証機能を持たないこのアプリの判断メモやデータが誰からでも見える状態になる。同じ理由で、ポート開放・リバースプロキシ・トンネルサービス等による公開もしない。
- 現バージョンは Host ヘッダーを `127.0.0.1`／`localhost` に限定している（DNSリバインディング対策）。Tailscale Serve 経由では Host が `<マシン名>.<tailnet>.ts.net` になるため、そのままでは 400 で拒否される。使う場合は、`backend/stockapp/config.py` の `allowed_hosts` に**自分の tailnet のホスト名だけ**を追加する（ワイルドカードは使わない）。接続元の検査（ループバックのみ許可）は、Serve がローカルに中継するためそのまま有効。

## 暫定値・既知の制約

| 項目 | 内容 |
|---|---|
| 業種感応タグ（17業種） | `content/sector_sensitivity.json` の各セルは業種の一般的な説明に沿って記入したもので、調査本文（research-03）の原表との突き合わせが**未了**（要件 9.1-6） |
| J-Quants Light の制約 | 財務科目明細に依存する指標、日経平均等の汎用指数、信用残・空売り比率、セクター別指数は取得できないため扱わない。決算サマリーは期首からの累計値で、四半期単独値は差分で算出する。PBR・時価総額は推定値を含む。履歴は最大5年 |
| FRED の遅れ | ドル円（DEXJPUS）は日次値だが更新は週次（最大約1週間遅れ）。日本10年金利・コールレートは月次（約1か月遅れ）。日銀の政策金利系列は2023年12月で停止のため、月次コールレートで代替 |
| 決算発表予定 | 決算発表予定APIは3・9月期決算会社・翌営業日分のみのため使っていない |
| ウォッチリスト上限 | 30銘柄（暫定、`content/watch_rules.json`） |
| ダイジェストの変動幅 | 直近60営業日の日次騰落率の標準偏差×2（暫定、同上） |
| 確認ポイントの閾値 | 進捗率の乖離 ±15ポイントなど（暫定、`content/fin_rules.json`。画面の第3階層に表示） |
| 短観の想定為替レート | 手入力の設定値（初期値 154.23円・2026年9月調査）。四半期ごとに S5 で更新する |
| 改変不可の範囲 | アプリ経由の変更・削除はDBトリガーで拒否し、改変はハッシュで検知する。DBファイルの直接編集そのものは防げない |

## ディレクトリ

```
app/
├── start.sh / start.bat / run_tests.sh / .env.example
├── backend/
│   ├── requirements.txt         fastapi, uvicorn, httpx, pandas, pytest
│   ├── stockapp/
│   │   ├── config.py            .env 読み込み（キーは repr に出さない）・許可ホスト
│   │   ├── db.py                SQLite 接続（WAL）＋マイグレーション（python -m stockapp.db）
│   │   ├── migrations/          0001〜0005（0005：判断メモ・ウォッチリスト・改変拒否トリガー）
│   │   ├── jquants/             J-Quants v2 クライアント・レート制限キュー
│   │   ├── ingest.py / bars.py / fins.py / fred.py   取得ジョブ・夜間バッチ・追いかけ取得
│   │   ├── technicals.py / indicators.py             テクニカル計算と事実文
│   │   ├── financials.py / fundamentals.py           業績・財務・バリュエーション・確認ポイント
│   │   ├── macro.py / app_settings.py                マクロ帯・業種感応・設定値・S0確認状態
│   │   ├── memos.py / watchlist.py / backup.py       判断メモ・ウォッチリストとダイジェスト・世代バックアップ
│   │   ├── wording.py           文言テンプレートと禁止語ガード
│   │   ├── content/             文言・用語解説・閾値・フォーム定義・S0文面（禁止語テスト対象）
│   │   ├── scheduler.py         起動時の追いかけ取得と夜間バッチ（20:00 JST）
│   │   ├── main.py              FastAPI（/api と静的配信、ループバック限定）
│   │   └── server.py            起動（127.0.0.1 固定）
│   └── tests/                   pytest
├── frontend/                    Vite + React + TypeScript + ECharts（src/pages, src/components）
├── docs/api-field-mapping.md    論理名 ↔ J-Quants v2 フィールド対応表
└── data/                        app.db と backup/（実行時に作成・Git管理外・共有しない）
```
