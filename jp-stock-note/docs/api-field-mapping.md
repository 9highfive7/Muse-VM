# APIフィールド対応表（J-Quants v2 → アプリ論理名）

フェーズ0成果物（要件 4.1「APIの実フィールド名と本書の論理名との対応表」）。
取得層はこの表に従って変換する。J-Quants側の名前が変わった場合は、`backend/stockapp/ingest.py` とこの表だけを直す。

## 根拠の区分

| 記号 | 意味 |
|---|---|
| ✅実測 | 2026-11-21 のフェーズ0実測（トヨタ 72030、2026-11-01〜20）の生レスポンスで確認したフィールド名・値の形 |
| 🟡未確認 | 実測ログが途中で切れている、または未取得。v2仕様の命名規則から推定したもの。APIキー入手後に最初の実取得で確認し、この表を更新する |

共通事項（✅実測）

- 認証：`.env` の `JQUANTS_API_KEY` を `x-api-key` ヘッダーで送る。ベースURL `https://api.jquants.com`。サーバー側の取得層だけが使う。
- 応答：`{"data": [...], "pagination_key": "..."}`。`pagination_key` があれば同じクエリに付けて次ページを取得する。
- 銘柄コードは5桁（4桁コード＋末尾1桁、通常 `0`）。例：`72030`。画面では末尾が0なら4桁で表示する。
- 決算サマリーの数値は文字列で届き、未開示は空文字 `""`。アプリでは空文字を NULL として保存する（0にしない）。

## 銘柄マスタ `/v2/equities/master` → `stock_master`

主キー：コード＋適用日。週1回全件更新（要件 4.1）。

| アプリ論理名（列） | 意味 | v2フィールド | 根拠 | 備考 |
|---|---|---|---|---|
| code | 銘柄コード | `Code` | ✅実測 | `"72030"` |
| date | 適用日 | `Date` | ✅実測 | 11-21取得時に `"2026-11-24"`（翌営業日分）が返った。検索・ヘッダーは「今日以前で最新の適用日」の行を使い、なければ最も古い行を使う |
| name | 銘柄名 | `CoName` | ✅実測 | 全角英数を含む（例：`ＫＤＤＩ`）。検索用に NFKC＋casefold した `name_norm` も保存 |
| name_en | 英文銘柄名 | `CoNameEn` | ✅実測 | 検索対象（`name_en_norm`） |
| sector17_code / sector17_name | 17業種 | `S17` / `S17Nm` | ✅実測 | `"6"` / `"自動車・輸送機"` |
| sector33_code / sector33_name | 33業種 | `S33` / `S33Nm` | ✅実測 | `"3700"` / `"輸送用機器"` |
| scale_category | TOPIX規模区分 | `ScaleCat` | ✅実測 | `"TOPIX Core30"` |
| market_code / market_name | 市場区分 | `Mkt` / `MktNm` | ✅実測 | `"0111"` / `"プライム"` |
| margin_code / margin_name | 貸借信用区分 | `Mrgn` / `MrgnNm` | ✅実測 | `"2"` / `"貸借"` |
| （raw_json に保持） | 商品区分 | `ProdCat` | ✅実測 | `"011"`。専用列は持たない |

## 営業日カレンダー `/v2/markets/calendar` → `trading_calendar`

クエリ：`from`, `to`（YYYY-MM-DD）。月1回更新。

| アプリ論理名 | 意味 | v2フィールド | 根拠 | 備考 |
|---|---|---|---|---|
| date | 日付 | `Date` | ✅実測 | |
| holiday_division | 休日区分 | `HolDiv` | ✅実測 | 文字列。日曜 2026-11-01 で `"0"` を確認 |
| is_business_day | 営業日フラグ（アプリ算出） | `HolDiv` から算出 | 🟡未確認 | 区分値の意味はv1と同じと仮定：`0`=非営業日、`1`=営業日、`2`=東証半日立会日、`3`=非営業日（祝日取引あり）。`1`・`2` を営業日とする |

## 株価日足 `/v2/equities/bars/daily` → `daily_bars`（フェーズ1で取得実装）

主キー：コード＋日付。調整後・未調整の両方を保存（要件 4.1）。

| アプリ論理名 | 意味 | v2フィールド | 根拠 | 備考 |
|---|---|---|---|---|
| code / date | コード / 日付 | `Code` / `Date` | ✅実測 | |
| open / high / low / close | 未調整 始値・高値・安値・終値 | `O` / `H` / `L` / `C` | ✅実測 | 数値（float） |
| upper_limit / lower_limit | ストップ高・安フラグ | `UL` / `LL` | ✅実測 | 文字列 `"0"`/`"1"` |
| volume | 未調整出来高 | `Vo` | ✅実測 | |
| turnover | 売買代金 | `Va` | ✅実測 | 円 |
| adj_factor | 調整係数 | `AdjFactor` | ✅実測 | 分割日に ≠1。分割検知・再取得トリガー（要件 4.2 ⑤） |
| adj_open / adj_high / adj_low / adj_close | 調整後OHLC | `AdjO` / `AdjH` / `AdjL` / `AdjC` | ✅実測 | 前日比・テクニカルは `AdjC` を使う（要件 3.1, 5章） |
| adj_volume | 調整後出来高 | `AdjVo` | ✅実測 | |
| market_cap | 時価総額 | `MktCap` | ✅実測（単位は推定） | トヨタで `44529307.0`。終値3,051円との比較から**百万円単位**と推定。フェーズ2のPER/PBRで使う前に要確認 |
| （未保存） | 不明 | `ExRT` | ✅実測（意味未確認） | 実測値は `null`。意味が確定するまで列を作らない |

前場・後場の内訳など上記以外のフィールドは、Lightプランの実測応答に含まれていなかった。

## TOPIX日足 `/v2/indices/bars/daily/topix` → `topix_bars`（フェーズ1）

| アプリ論理名 | v2フィールド | 根拠 |
|---|---|---|
| date | `Date` | ✅実測 |
| open / high / low / close | `O` / `H` / `L` / `C` | ✅実測 |

汎用の `/v2/indices/bars/daily` は Light で 403（✅実測）。使わない。

## 決算サマリー `/v2/fins/summary` → `fins_summary`（フェーズ2）

主キー：開示番号。生レスポンス1件ごとに `raw_json` と `content_sha256`（訂正検知用、要件 4.6）を保存する。
実測ログは配当欄の途中で切れているため、それ以降は🟡。

| アプリ論理名 | 意味 | v2フィールド | 根拠 | 備考 |
|---|---|---|---|---|
| disclosure_no | 開示番号 | `DiscNo` | ✅実測 | `"20220204580837"` |
| code | コード | `Code` | ✅実測 | |
| disclosed_date / disclosed_time | 開示日 / 開示時刻 | `DiscDate` / `DiscTime` | ✅実測 | 決算開示日マーカーの元（要件 3.2） |
| doc_type | 書類種別 | `DocType` | ✅実測 | 例 `3QFinancialStatements_Consolidated_IFRS`（会計基準・連結区分を含む。単独化の成立条件判定に使う） |
| period_type | 期間種別 | `CurPerType` | ✅実測 | `1Q`/`2Q`/`3Q`/`FY` |
| period_start / period_end | 当期間 開始・終了 | `CurPerSt` / `CurPerEn` | ✅実測 | |
| fy_start / fy_end | 当事業年度 開始・終了 | `CurFYSt` / `CurFYEn` | ✅実測 | |
| （raw_json） | 翌事業年度 開始・終了 | `NxtFYSt` / `NxtFYEn` | ✅実測 | |
| net_sales | 売上高（累計） | `Sales` | ✅実測 | 円、文字列 |
| operating_profit | 営業利益（累計） | `OP` | ✅実測 | |
| ordinary_profit | 経常利益（累計） | `OdP` | ✅実測 | IFRS企業は空文字 |
| profit | 純利益（累計） | `NP` | ✅実測 | |
| eps | EPS | `EPS` | ✅実測 | 希薄化後は `DEPS`（raw_json） |
| total_assets | 総資産 | `TA` | ✅実測 | |
| equity | 自己資本 | `Eq` | ✅実測 | |
| equity_ratio | 自己資本比率 | `EqAR` | ✅実測 | **比率（0.392 = 39.2%）**。表示時に×100 |
| bps | BPS | `BPS` | ✅実測 | 空文字の期あり |
| cf_operating / cf_investing / cf_financing | 営業・投資・財務CF | `CFO` / `CFI` / `CFF` | ✅実測 | 1Q・3Qは空文字が多い（要件 3.5） |
| cash_equivalents | 現金同等物 | `CashEq` | ✅実測 | |
| （raw_json） | 配当（四半期別・期末・年間・単位） | `Div1Q` / `Div2Q` / `Div3Q` / `DivFY` / `DivAnn` / `DivUnit` | ✅実測（名前のみ） | 値の形は未確認 |
| forecast_net_sales | 通期予想 売上高 | `FSales` | ✅実測 | J-Quantsコネクター記録（custom.jquants）で確認 |
| forecast_operating_profit | 通期予想 営業利益 | `FOP` | ✅実測 | 同上 |
| （フェーズ2で列追加） | 通期予想 経常利益・純利益・EPS・配当 | `FOdP` / `FNP` / `FEPS` / `FDivAnn` 等 | 🟡未確認 | 命名規則からの推定。フェーズ2の最初の実取得で確定し、列はマイグレーション 0002 で追加する |

`from`/`to` によるフィルタはコード指定時に効かなかった記録がある（コネクター記録）。コード指定は「プラン範囲の全期間」が返る前提で扱う。

## 決算発表予定 `/v2/equities/earnings-calendar`（取得・蓄積のみ、表示は将来）

| 意味 | v2フィールド | 根拠 |
|---|---|---|
| 予定日 / コード / 銘柄名 | `Date` / `Code` / `CoName` | ✅実測 |
| 決算期 / 四半期 / 業種名 / 市場 | `FY` / `FQ` / `SectorNm` / `Section` | ✅実測（値は全角表記。例 `"第２四半期"`） |

## エンドポイント可否（Lightプラン、✅実測）

| エンドポイント | 結果 | アプリでの扱い |
|---|---|---|
| `/v2/equities/master` | 取得可 | フェーズ0で実装 |
| `/v2/markets/calendar` | 取得可 | フェーズ0で実装 |
| `/v2/equities/bars/daily` | 取得可 | フェーズ1 |
| `/v2/indices/bars/daily/topix` | 取得可 | フェーズ1 |
| `/v2/fins/summary` | 取得可 | フェーズ2 |
| `/v2/equities/earnings-calendar` | 取得可 | 蓄積のみ |
| `/v2/indices/bars/daily` | 403 | 使わない（403はプラン制限として記録、再試行しない） |
| `/v2/fins/details` | 403 | 使わない |

## FRED `fredgraph.csv` → `fred_observations` / `fred_series`（フェーズ3）

キー不要。`https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>&cosd=<開始日>` を**1系列ずつ**取得する（複数IDを指定するとZIPが返る：フェーズ0実測。ZIP応答は保存せずエラー記録）。取得層は `backend/stockapp/fred.py`。

| 項目 | 形 | 根拠 | アプリでの扱い |
|---|---|---|---|
| ヘッダー | `observation_date,<ID>`（旧形式は `DATE,<ID>`） | ✅実測（2026-11-21） | 2列目が系列IDと一致しなければ形式エラー（`parse_error`） |
| 観測日 | `YYYY-MM-DD`。月次系列は各月1日 | ✅実測 | `fred_observations.date` |
| 値 | 数値文字列。欠損は空欄（旧形式は `.`） | ✅実測（例 `2025-07-04,`） | 空欄・`.` とも NULL で保存（0にしない） |
| 並び・重複 | 通常は日付昇順 | — | 保存前に日付で整列し、重複は最後の行を採用 |

| 系列 | ID | 頻度 | 取得 | 遅れ（research-03） |
|---|---|---|---|---|
| 米10年金利 | `DGS10` | 日次 | 毎営業日（直近6年分） | 米国の前営業日分まで |
| 米2年金利 | `DGS2` | 日次 | 毎営業日（直近6年分） | 同上（長短スプレッドは両方に値がある日だけで算出） |
| ドル円 | `DEXJPUS` | 日次値 | 毎営業日（直近6年分） | 週次更新で最大約1週遅れ（実測：11-21時点で11-13まで） |
| 日本10年金利 | `IRLTLT01JPM156N` | 月次（OECD） | 週1回（全期間） | 約半月〜1か月遅れ |
| 日本短期金利 | `IRSTCI01JPM156N` | 月次（OECD、コールレート） | 週1回（全期間） | 同上。政策金利系列 IRSTCB01 は2023-12で停止のため代替（要件 9.1 未決事項7をこの系列で確定、2026-11-21に最新2026-10-01を確認） |
