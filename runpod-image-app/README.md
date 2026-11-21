# RunPod かんたん画像生成

RunPod の GPU Pod で動く **ComfyUI** を、ノード画面を触らずに **フォームだけで** 使うための Web アプリです。
初心者向けに、すべての設定項目の下に「一行説明」「少ない／おすすめ／多い の目安」「今の値の判定（例：CFG 12 →『強め：色が濃くギラつきやすい』）」が表示されます。

- 画面はすべて日本語・スマホ対応・フレームワークなし
- Node.js 24 だけで動きます（**外部依存ゼロ**、`npm install` 不要）

## 起動方法

```bash
cd runpod-image-app
npm start
```

ブラウザで <http://localhost:3000> を開きます。

- ポートを変える：`PORT=8080 npm start`
- 既定では `127.0.0.1`（自分のPCだけ）で待ち受けます。`HOST=0.0.0.0` で外部公開もできますが、下の「セキュリティ」を読んでから使ってください。

## RunPod での使い方

1. RunPod で **ComfyUI のテンプレート**を選んで Pod を **Deploy** する
2. Pod が Running になったら **Connect** を押す
3. ポート **8188**（HTTP Service）の URL をコピーする（例：`https://abc123xyz-8188.proxy.runpod.net`）
4. このアプリの「1. 接続」に URL（または Pod ID だけ）を貼って **接続テスト** → GPU名と VRAM が表示される
5. モデルを選び、プロンプトを書いて **生成**（迷ったらプリセット「まずはこれ（おすすめ）」）

使い終わったら RunPod で **Pod を Stop / Terminate すると課金が止まります**（Stop でもディスク保管料は少しかかります。Terminate すると Pod 上の画像も消えます）。

### 画面の構成

| セクション | 内容 |
|---|---|
| 使い方 | 上の手順と注意事項 |
| 1. 接続 | URL／Pod ID、任意のユーザー名・パスワード、接続テスト（GPU/VRAM表示）、モデル一覧＋手入力欄 |
| 2. 生成の設定 | プロンプト、ネガティブ、サイズ（プリセット＋数値）、Steps、CFG、サンプラー、スケジューラー、シード（ランダム可）、枚数。各項目に初心者ガイドと3色ゾーンバー |
| プリセット | 「まずはこれ（おすすめ）」「速く試す」「じっくり高品質」「プロンプトに忠実に」。押すと値が入り、組み合わせの理由が一行で出る（シードは変えない） |
| 生成 | 送信後2秒ごとに状態を確認（最大約10分）、完了したら画像とダウンロードリンクを表示。使ったシードを表示し「このシードで固定」できる |
| 3. 履歴 | 最新20件をブラウザの localStorage に保存（日時・プロンプト・設定・シード・画像の場所）。押すと設定がフォームに戻る |
| 困ったとき | 症状 → 対処の早見表 |

## しくみ（アーキテクチャ）

```
ブラウザ (public/)  ──JSON──▶  このサーバー (server.js)  ──HTTPS──▶  RunPod の ComfyUI (:8188)
```

- ブラウザから ComfyUI を直接呼ぶと **CORS** で止まるため、サーバーが ComfyUI API を中継します。
- ワークフローは **サーバー側で組み立てます**（`lib/comfy.js` の `buildWorkflow`）。
  `CheckpointLoaderSimple → CLIPTextEncode（ポジ／ネガ） → EmptyLatentImage → KSampler → VAEDecode → SaveImage（接頭辞 "kantan"）`
- 設定値はサーバーでも検証します（Steps 1〜100、CFG 1〜20、幅・高さ 256〜2048、枚数 1〜4、シードは整数。`-1`/空ならサーバーが 0〜2^53-1 の乱数を決め、実際に使った値を返す）。

| ファイル | 役割 |
|---|---|
| `server.js` | HTTP サーバー、API、ComfyUI への中継、画像のストリーミング中継 |
| `lib/comfy.js` | URL 正規化、設定検証、ワークフロー生成、ComfyUI 応答の解釈（純粋関数） |
| `lib/static.js` | `public/` の静的配信 |
| `public/` | 画面（`index.html` / `app.js` / `style.css`） |
| `test/` | `node:test` によるテスト |

### API（すべて JSON、エラーは `{ ok:false, error:"日本語メッセージ" }`）

| メソッド・パス | 送るもの | ComfyUI 側 | 返すもの |
|---|---|---|---|
| `POST /api/test-connection` | `{connection}` | `GET /system_stats` | GPU名、VRAM(GB)、ComfyUIバージョン |
| `POST /api/models` | `{connection}` | `GET /object_info/CheckpointLoaderSimple` | モデル名一覧 |
| `POST /api/generate` | `{connection, settings}` | `POST /prompt` | `prompt_id`、使ったシード |
| `POST /api/status` | `{connection, prompt_id}` | `GET /history/{id}` | `running` / `done`＋画像一覧 / `error`＋理由 |
| `GET /api/image?filename=&subfolder=&type=` | — | `GET /view` | 画像そのもの（中継） |

`connection` は `{ url, username, password }`。`url` は `https://<podid>-<port>.proxy.runpod.net` 形式か Pod ID（→ 8188 の URL に変換）。

## セキュリティ・注意

- **自分のPCで個人的に使う前提**です。既定で localhost のみで待ち受け、別サイトからの API 呼び出し（Origin 不一致）や localhost 以外の Host ヘッダーは拒否します。`HOST=0.0.0.0` で公開すると、同じネットワークの人があなたの Pod を使えてしまうので避けてください。
- **Pod の URL は秘密扱い**にしてください。URL を知っている人は誰でもその Pod（GPU）を使えます。
- 接続情報（URL・ユーザー名・パスワード）は **サーバーのメモリ上だけ**に保持し、ディスクには書きません（サーバーを止めると消えます）。ブラウザ側もパスワードは保存しません（URL はタブを閉じると消える sessionStorage にだけ保持）。
- 接続先は RunPod のプロキシ（`*.proxy.runpod.net`）に限定しています。
- **画像は Pod 上に保存されます。** 履歴に残るのは設定と画像の場所だけなので、Pod を削除すると画像は見られなくなります。残したい画像はダウンロードしてください。

## テスト

```bash
npm test
```

`node --test` で `test/` 以下を実行します。

- `test/comfy.test.js`：`normalizeBaseUrl`（完全URL・末尾スラッシュ・Pod ID・不正入力）、`buildWorkflow`（各ノードの値と配線の整合性）、設定検証、ComfyUI 応答の解釈
- `test/server.test.js`：偽の ComfyUI（差し替えた fetch）を相手に API 全体を確認

実際の Pod での生成確認は、Pod を起動したときに手動で行ってください。
