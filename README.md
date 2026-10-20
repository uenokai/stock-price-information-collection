# stock-price-information-collection

中長期投資向けの銘柄発掘システム。ファンダメンタル分析で「良い銘柄」を絞り込み、テクニカル分析で「買いタイミング」を検知して通知する。さらに、出したシグナルの**その後の成績を自動で追跡**し、ルールの**過去検証 (バックテスト)** を毎週行う。

```
[全ユニバース (約220銘柄: 日本株+米国株)]
      │  週次: ファンダメンタル・スクリーニング (ROE・成長率・PER等を業種内で相対評価)
      ▼
[ウォッチリスト (上位60銘柄)] ──────────────────┐ 週次: バックテスト (同じルールを過去5年に適用)
      │  日次: テクニカル・シグナル判定            │   → reports/backtest.md
      │  (ゴールデンクロス・52週高値ブレイク・押し目・出来高急増)
      ▼
[買い候補] ─┬→ Markdownレポート + Slack通知 (新規シグナルのみ)
            └→ シグナル台帳 (data/signal_log.csv) に記録
                     │  日次: 記録済みシグナルの +20/+60/+120 営業日後リターンを集計
                     ▼
            [成績表 reports/performance.md]  ← ルールが機能しているかを数字で確認する
```

詳細な設計は [docs/system-design.md](docs/system-design.md) を参照。

## 自動実行 (GitHub Actions)

| ワークフロー | スケジュール | 内容 |
|---|---|---|
| [Weekly Screening](.github/workflows/weekly-screen.yml) | 毎週土曜 07:00 JST | 全ユニバースをスクリーニングし `data/watchlist.csv` と `reports/watchlist.md` を更新。続けて新ウォッチリストでバックテストを行い `reports/backtest.md` を更新 |
| [Daily Signals](.github/workflows/daily-signals.yml) | 平日 08:00 JST | ウォッチリストの買いシグナルを判定し `reports/latest-signals.md` を更新。成立シグナルを `data/signal_log.csv` に記録し、過去シグナルの成績 `reports/performance.md` を更新。新規シグナルがあれば Slack 通知 |
| [Tests](.github/workflows/tests.yml) | push / PR 時 | 単体・統合テスト |

- どちらも **Actions タブから手動実行 (workflow_dispatch) 可能**。初回はまず Weekly Screening を手動実行するとウォッチリストが生成される (Daily Signals はウォッチリスト未生成なら自動でスクリーニングも実行する)。
- スケジュール実行はリポジトリの**デフォルトブランチにマージされてから**有効になる。
- **注意: GitHub は 60 日間コミットが無いリポジトリのスケジュール実行を自動停止する。** 結果コミットが毎日入っていれば止まらないが、止まった場合は Actions タブで各ワークフローを "Enable workflow" する。
- ワークフロー失敗時は `SLACK_WEBHOOK_URL` が設定されていれば Slack に失敗通知が届く。

### Slack通知の設定 (任意)

リポジトリの Settings → Secrets and variables → Actions に `SLACK_WEBHOOK_URL` (Slack Incoming Webhook のURL) を登録すると、買いシグナル成立時に通知が届く。未設定でもレポート生成は行われる。

通知は**新規シグナルのみ** (同じ銘柄・同じシグナルが直近 10 営業日に出ていなかったもの)。押し目のように連日成立するシグナルで毎日通知が来ることを防ぐ。継続中のシグナルはレポートで確認できる。`config.yaml` の `signals.notify_only_new: false` で毎日通知に戻せる。

## ローカルでの実行

```bash
pip install -e ".[dev]"

# 週次: スクリーニング → ウォッチリスト生成 (全銘柄で5分前後)
stock-screener screen
stock-screener screen --limit 20          # 動作確認用に銘柄数を絞る

# 日次: 買いシグナル判定 → 台帳記録 → 成績表更新 → レポート生成
stock-screener signals                    # Slack通知あり (SLACK_WEBHOOK_URL 設定時)
stock-screener signals --no-notify

# バックテスト: 本番と同じルールを過去5年に適用して成績を集計
stock-screener backtest                   # 現在のウォッチリストが対象
stock-screener backtest --universe        # 全ユニバースが対象 (選定バイアスを減らしたい場合)
stock-screener backtest --period 10y      # 期間を変える

# テスト
python -m pytest
```

## 成果物

| ファイル | 内容 | 更新 |
|---|---|---|
| `data/watchlist.csv` | 現在のウォッチリスト (スコア・各指標付き) | 週次 |
| `data/signal_log.csv` | シグナル台帳。成立した (判定日, 銘柄, シグナル) を全て記録。`is_new` 列が新規/継続 | 日次 |
| `reports/watchlist.md` | 週次スクリーニング結果。前回からの入れ替え (新規追加/除外) も表示 | 週次 |
| `reports/latest-signals.md` | 最新の買いシグナルレポート。🆕 が新規、無印は継続中。ファンダスコア併記 | 日次 |
| `reports/signals/YYYY-MM-DD.md` | 日付別のシグナル履歴 | 日次 |
| `reports/performance.md` | **成績表**: 実際に出した新規シグナルの +20/+60/+120 営業日後の勝率・平均リターン | 日次 |
| `reports/backtest.md` | **バックテスト**: 過去5年にルールを適用した場合の成績。ベースライン (全営業日に買った場合) との差 = 「超過」が、そのシグナルのタイミング価値 | 週次 |

### 成績表とバックテストの違い

- **performance.md** は「このシステムが実際に出したシグナル」の追跡。記録が溜まるまで (目安 30 件以上) は参考値。
- **backtest.md** は「同じルールを過去に当てたらどうだったか」。すぐに件数が取れるが、対象銘柄が*今の*財務指標で選ばれているため生存バイアスが入る。検証できるのは主に第2段階 (タイミング) のルール。
- どちらも手数料・配当を無視した終値ベースの単純比較。

## カスタマイズ

- **監視銘柄の追加・削除**: `data/universe/japan.csv` / `data/universe/us.csv` を編集 (ticker は yfinance 形式。例: `7203.T`, `AAPL`, `0700.HK`)。別市場を足す場合は CSV を追加して `config.yaml` の `universe:` に登録する。
- **スコアの重み・ウォッチリスト銘柄数・シグナルの閾値・新規判定の間隔・成績の評価期間**: すべて [config.yaml](config.yaml) で調整可能。

## 構成

```
src/stock_screener/
├── cli.py                  # コマンドラインエントリポイント (screen / signals / backtest)
├── config.py               # config.yaml の読み込み
├── universe.py             # ユニバースCSVの読み込み
├── data/
│   ├── prices.py           # 株価履歴の取得 (yfinance + 期間別Parquetキャッシュ)
│   └── fundamentals.py     # 財務指標の取得 (yfinance)
├── screening/
│   └── fundamental.py      # 業種内パーセンタイルによる合成スコアリング
├── signals/
│   ├── indicators.py       # SMA / RSI (Wilder) / 期間高値
│   └── technical.py        # 買いシグナル判定 (全営業日ベクトル計算。日次判定とバックテストで共用)
├── tracking.py             # シグナル台帳の更新と N営業日後リターンの集計
├── backtest.py             # ルールの過去検証 (ベースライン比較付き)
├── report.py               # Markdownレポート生成
└── notify.py               # Slack通知
```

## 今後の予定 (Phase 4)

- 成績表の件数が溜まったら、勝率・超過リターンの低いシグナルの閾値見直し・削除
- ユニバース拡大: 東証全銘柄 (J-Quants API)、欧州・アジア市場
- 業績修正・ニュースなどイベント系シグナルの追加
- 第1段階 (ファンダ選定) 自体の検証: 過去時点の財務スナップショット (`data/fundamentals/`) を使ったウォッチリストの事後リターン比較

## 免責事項

本システムは機械的なスクリーニング・シグナル判定の結果を提示するものであり、投資助言ではなく、利益を保証するものでもありません。投資判断は必ずご自身の責任で行ってください。また、無料データソース (yfinance) を利用しているため、データの遅延・欠損・仕様変更が起こり得ます。
