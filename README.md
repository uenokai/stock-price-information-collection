# stock-price-information-collection

中長期投資向けの銘柄発掘システム。ファンダメンタル分析で「良い銘柄」を絞り込み、テクニカル分析で「買いタイミング」を検知して通知する。

```
[全ユニバース (約220銘柄: 日本株+米国株)]
      │  週次: ファンダメンタル・スクリーニング (ROE・成長率・PER等を業種内で相対評価)
      ▼
[ウォッチリスト (上位60銘柄)]
      │  日次: テクニカル・シグナル判定 (ゴールデンクロス・52週高値ブレイク・押し目・出来高急増)
      ▼
[買い候補] ──→ Markdownレポート + Slack通知
```

詳細な設計は [docs/system-design.md](docs/system-design.md) を参照。

## 自動実行 (GitHub Actions)

| ワークフロー | スケジュール | 内容 |
|---|---|---|
| [Weekly Screening](.github/workflows/weekly-screen.yml) | 毎週土曜 07:00 JST | 全ユニバースをスクリーニングし、`data/watchlist.csv` と `reports/watchlist.md` を更新 |
| [Daily Signals](.github/workflows/daily-signals.yml) | 平日 08:00 JST | ウォッチリストの買いシグナルを判定し、`reports/latest-signals.md` を更新。シグナル成立時はSlack通知 |
| [Tests](.github/workflows/tests.yml) | push / PR 時 | 単体・統合テスト |

- どちらも **Actions タブから手動実行 (workflow_dispatch) 可能**。初回はまず Weekly Screening を手動実行するとウォッチリストが生成される (Daily Signals はウォッチリスト未生成なら自動でスクリーニングも実行する)。
- スケジュール実行はリポジトリの**デフォルトブランチにマージされてから**有効になる。

### Slack通知の設定 (任意)

リポジトリの Settings → Secrets and variables → Actions に `SLACK_WEBHOOK_URL` (Slack Incoming Webhook のURL) を登録すると、買いシグナル成立時に通知が届く。未設定でもレポート生成は行われる。

## ローカルでの実行

```bash
pip install -e ".[dev]"

# 週次: スクリーニング → ウォッチリスト生成 (全銘柄で5分前後)
stock-screener screen

# 動作確認用に銘柄数を絞る
stock-screener screen --limit 20

# 日次: 買いシグナル判定 → レポート生成
stock-screener signals            # Slack通知あり (SLACK_WEBHOOK_URL 設定時)
stock-screener signals --no-notify

# テスト
pytest
```

## 成果物

| ファイル | 内容 |
|---|---|
| `data/watchlist.csv` | 現在のウォッチリスト (スコア・各指標付き) |
| `reports/watchlist.md` | 週次スクリーニング結果レポート |
| `reports/latest-signals.md` | 最新の買いシグナルレポート |
| `reports/signals/YYYY-MM-DD.md` | 日付別のシグナル履歴 |

## カスタマイズ

- **監視銘柄の追加・削除**: `data/universe/japan.csv` / `data/universe/us.csv` を編集 (ticker は yfinance 形式。例: `7203.T`, `AAPL`, `0700.HK`)。別市場を足す場合は CSV を追加して `config.yaml` の `universe:` に登録する。
- **スコアの重み・ウォッチリスト銘柄数・シグナルの閾値**: すべて [config.yaml](config.yaml) で調整可能。

## 構成

```
src/stock_screener/
├── cli.py                  # コマンドラインエントリポイント (screen / signals)
├── config.py               # config.yaml の読み込み
├── universe.py             # ユニバースCSVの読み込み
├── data/
│   ├── prices.py           # 株価履歴の取得 (yfinance + Parquetキャッシュ)
│   └── fundamentals.py     # 財務指標の取得 (yfinance)
├── screening/
│   └── fundamental.py      # 業種内パーセンタイルによる合成スコアリング
├── signals/
│   ├── indicators.py       # SMA / RSI (Wilder) / 期間高値
│   └── technical.py        # 買いシグナル判定
├── report.py               # Markdownレポート生成
└── notify.py               # Slack通知
```

## 今後の予定 (Phase 3以降)

- バックテスト: スクリーニング+シグナルのルールを過去データで検証する
- ユニバース拡大: 東証全銘柄 (J-Quants API)、欧州・アジア市場
- 業績修正・ニュースなどイベント系シグナルの追加

## 免責事項

本システムは機械的なスクリーニング・シグナル判定の結果を提示するものであり、投資助言ではなく、利益を保証するものでもありません。投資判断は必ずご自身の責任で行ってください。また、無料データソース (yfinance) を利用しているため、データの遅延・欠損・仕様変更が起こり得ます。
