# stock-price-information-collection

中長期投資向けの銘柄発掘システム。**大目的は投資リターンの最大化**なので、最上位の評価指標は「指数ETFに勝てるか」に置く。

1. **何を買うか**: 財務指標 (ROE・成長率・PER等) と株価モメンタムを業種内で相対評価し、上位60銘柄のウォッチリストを週次で作る
2. **過去検証**: 同じ選び方を過去に当てた場合の成績を指数ETF (SPY / 1306.T) と比べる (週次)
3. **モデルポートフォリオ (ペーパー運用)**: 戦略に従うと「今なにを持つか」を具体的な銘柄リストにし、月初に入れ替え、評価額を指数と並べて日次で記録する。過去検証では測れない「その後の成績」がここに溜まる
4. **参考情報**: ウォッチリスト銘柄のテクニカル条件 (押し目等) を日次で判定する。バックテストでは買う日を決める価値がほぼ無いと分かったので、売買の引き金ではなく参考表示

```
[全ユニバース (約220銘柄: 日本株+米国株)]
      │  週次: 財務指標 + 12-1ヶ月モメンタム を業種内パーセンタイルで合成
      ▼
[ウォッチリスト (上位60銘柄)] ─┬─ 週次: ポートフォリオ・バックテスト (上位N銘柄の月次保有 vs 指数ETF)
      │                        │     → reports/portfolio-backtest.md  ← 指数に勝てなければ指数ETFを買う
      │                        └─ 日次: モデルポートフォリオ (市場別 上位15 × 2戦略を等金額保有、月初に入れ替え)
      │                              → reports/model-portfolio.md    ← 今なにを持つか + その後の成績 vs 指数
      │  日次: テクニカル条件の判定 (参考情報)
      ▼
[reports/latest-signals.md] ──→ data/signal_log.csv に記録
                                    │  日次: +20/+60/+120 営業日後リターンを集計
                                    ▼
                              [reports/performance.md]
```

出力はすべて Markdown でリポジトリにコミットされる。通知は現在行わない (Slack Webhook を設定しない限り何も送らない)。Obsidian で読む場合は vault 内にこのリポジトリを clone するか Obsidian Git プラグインで pull し、[reports/index.md](reports/index.md) を入口にする。

詳細な設計は [docs/system-design.md](docs/system-design.md) を参照。

## 自動実行 (GitHub Actions)

| ワークフロー | スケジュール | 内容 |
|---|---|---|
| [Weekly Screening](.github/workflows/weekly-screen.yml) | 毎週土曜 07:00 JST | 全ユニバースをスクリーニングし `data/watchlist.csv` と `reports/watchlist.md` を更新。財務スナップショットを `data/fundamentals/` に保存。続けてシグナルのバックテスト (`reports/backtest.md`) とポートフォリオ・バックテスト (`reports/portfolio-backtest.md`) を更新 |
| [Daily Signals](.github/workflows/daily-signals.yml) | 平日 08:00 JST | ウォッチリストのテクニカル条件を判定し `reports/latest-signals.md` を更新。成立シグナルを `data/signal_log.csv` に記録し、過去シグナルの成績 `reports/performance.md` を更新。モデルポートフォリオを評価し (月初なら入れ替え) `reports/model-portfolio.md` を更新 |
| [Tests](.github/workflows/tests.yml) | push / PR 時 | 単体・統合テスト |

- どちらも **Actions タブから手動実行 (workflow_dispatch) 可能**。初回はまず Weekly Screening を手動実行するとウォッチリストが生成される (Daily Signals はウォッチリスト未生成なら自動でスクリーニングも実行する)。
- スケジュール実行はリポジトリの**デフォルトブランチにマージされてから**有効になる。
- **注意: GitHub は 60 日間コミットが無いリポジトリのスケジュール実行を自動停止する。** 結果コミットが毎日入っていれば止まらないが、止まった場合は Actions タブで各ワークフローを "Enable workflow" する。
- ワークフロー失敗時は `SLACK_WEBHOOK_URL` が設定されていれば Slack に失敗通知が届く。

### 通知 (現在はオフ)

通知は行わず、Markdown レポートの蓄積のみ。将来通知したくなったら、リポジトリの Settings → Secrets and variables → Actions に `SLACK_WEBHOOK_URL` を登録すると新規シグナル成立時とワークフロー失敗時に Slack へ送る。新規 = 同じ銘柄・同じシグナルが直近10営業日に出ていなかったもの (`config.yaml` の `signals.renotify_after_days`)。

## ローカルでの実行

```bash
pip install -e ".[dev]"

# 週次: スクリーニング → ウォッチリスト生成 (全銘柄で5分前後)
stock-screener screen
stock-screener screen --limit 20          # 動作確認用に銘柄数を絞る

# 日次: 買いシグナル判定 → 台帳記録 → 成績表更新 → レポート生成
stock-screener signals                    # Slack通知あり (SLACK_WEBHOOK_URL 設定時)
stock-screener signals --no-notify

# ポートフォリオ・バックテスト: 毎月上位N銘柄を等金額保有した場合 vs 指数ETF (過去10年)
stock-screener portfolio
stock-screener portfolio --period 15y

# モデルポートフォリオ (ペーパー運用): 評価と月初の入れ替え → reports/model-portfolio.md
stock-screener model-portfolio
stock-screener model-portfolio --rebalance   # 月が変わっていなくても入れ替える

# シグナル別バックテスト: 本番と同じルールを過去5年に適用して成績を集計
stock-screener backtest                   # 現在のウォッチリストが対象
stock-screener backtest --universe        # 全ユニバースが対象 (選定バイアスを減らしたい場合)

# テスト
python -m pytest
```

## 成果物

| ファイル | 内容 | 更新 |
|---|---|---|
| `reports/index.md` | 入口。各レポートの見る順番と現時点の結論 | 手動 |
| `reports/portfolio-backtest.md` | **指数に勝てるか (過去)**: 上位N銘柄の月次保有 (モメンタム / トレンド / 全銘柄等金額) を指数ETFと比較。CAGR・最大DD・暦年リターン | 週次 |
| `reports/model-portfolio.md` | **今なにを持つか + その後 (ペーパー運用)**: モメンタム戦略と合成スコア戦略の保有30銘柄 (日15/米15)、月初の入れ替え内容、開始日からの評価額 vs 指数 | 日次 |
| `data/model_portfolio/` | ペーパー運用の状態: `holdings.csv` (保有), `trades.csv` (入れ替え履歴), `nav.csv` (日次の評価額と指数) | 日次 |
| `data/watchlist.csv` | 現在のウォッチリスト (スコア・各指標付き) | 週次 |
| `data/fundamentals/YYYY-MM-DD.csv` | 週次の財務指標スナップショット。第1段階 (財務による選定) を将来検証するために蓄積 | 週次 |
| `data/signal_log.csv` | シグナル台帳。成立した (判定日, 銘柄, シグナル) を全て記録。`is_new` 列が新規/継続 | 日次 |
| `reports/watchlist.md` | 週次スクリーニング結果。前回からの入れ替え (新規追加/除外) も表示 | 週次 |
| `reports/latest-signals.md` | 最新の買いシグナルレポート。🆕 が新規、無印は継続中。ファンダスコア併記 | 日次 |
| `reports/signals/YYYY-MM-DD.md` | 日付別のシグナル履歴 | 日次 |
| `reports/performance.md` | **成績表**: 実際に出した新規シグナルの +20/+60/+120 営業日後の勝率・平均リターン | 日次 |
| `reports/backtest.md` | **バックテスト**: 過去5年にルールを適用した場合の成績。ベースライン (全営業日に買った場合) との差 = 「超過」が、そのシグナルのタイミング価値 | 週次 |

### 4つの検証の違い

- **model-portfolio.md** は「これから」の成績。開始後の実際の値動きで決まるので後から選んだ銘柄ではなく、バックテストの弱点 (生存バイアス) が無い。半年〜1年分溜まったら、対指数がプラスかで戦略を続けるか判断する
- **portfolio-backtest.md** は「過去」の成績。戦略としての成績 (CAGR・最大DD) を指数ETFと比べる。価格だけで決まる基準 (モメンタム・トレンド) は過去10年で検証済み。財務指標による基準は過去時点の値が無いため、`data/fundamentals/` が1年以上溜まってから検証する
- **backtest.md** はテクニカルシグナル1件ごとの N営業日後リターン。全営業日に買った場合との差 (超過) がほぼゼロ = タイミングの価値なし、が現時点の結果
- **performance.md** は実際に出したシグナルの追跡。記録が溜まるまで (目安30件以上) は参考値
- いずれも終値ベースの単純比較。ポートフォリオ・バックテストのみ入れ替えコスト (往復0.2%) を控除。税金・為替は未考慮

### 現時点の結論 (2026-10-20, 詳細は reports/)

- 12-1ヶ月モメンタム上位15銘柄の月次保有は、日本・米国とも指数ETFとユニバース等金額を上回った (日本 CAGR +24% vs 指数 +12%、米国 +22% vs +15%)。ボラティリティは 1.5 倍になる
- 200日線のトレンドフィルタは、リターンを下げる代わりに最大ドローダウンをわずかに改善する程度
- テクニカルの4シグナルは買う日を決める根拠にならない
- この結果を受けて、週次スクリーニングの合成スコアにモメンタムを重み 0.22 で加えた (`config.yaml`)

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
│   └── technical.py        # テクニカル条件の判定 (全営業日ベクトル計算。日次判定とバックテストで共用)
├── tracking.py             # シグナル台帳の更新と N営業日後リターンの集計
├── backtest.py             # シグナル別の過去検証 (ベースライン比較付き)
├── portfolio.py            # ポートフォリオ・バックテスト (月次上位N保有 vs 指数ETF) とモメンタム計算
├── model_portfolio.py      # モデルポートフォリオ (ペーパー運用): 選定・等金額リバランス・日次評価・状態CSV
├── report.py               # Markdownレポート生成
└── notify.py               # Slack通知
```

## 使い方 (運用の流れ)

見るのは `reports/model-portfolio.md` の1ファイルだけ。

1. 月に1回、月初の最初の平日の翌日以降に `model-portfolio.md` を開く。「成績」の表で対指数がプラスかを見る
2. 「直近の入れ替え」の買い・売りが、戦略に従う場合の売買指示に相当する (自動では発注しない)
3. 他のレポートは必要なときだけ。`reports/index.md` に用途を一覧してある
4. 半年〜1年経って対指数がマイナスなら、戦略を見直すか指数ETFに切り替える

## 今後の予定 (Phase 4)

- 第1段階 (財務指標による選定) の検証: `data/fundamentals/` の週次スナップショットが溜まり次第、「ウォッチリスト上位N銘柄の月次保有」をポートフォリオ・バックテストの枠組みで指数と比較する。勝てなければ財務指標の重みを下げ、モメンタム中心に寄せる
- ポートフォリオ・バックテストに保有銘柄数 (N) とリバランス頻度の感度分析を追加
- ユニバース拡大: 東証全銘柄 (J-Quants API)、欧州・アジア市場
- 通知手段の決定 (Obsidian / メール等)。現状は Markdown の蓄積のみ

## 免責事項

本システムは機械的なスクリーニング・シグナル判定の結果を提示するものであり、投資助言ではなく、利益を保証するものでもありません。投資判断は必ずご自身の責任で行ってください。また、無料データソース (yfinance) を利用しているため、データの遅延・欠損・仕様変更が起こり得ます。
