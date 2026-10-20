# CLAUDE.md

中長期投資向けの銘柄発掘システム。設計は `docs/system-design.md`、使い方は `README.md` を参照。

## コマンド

```bash
pip install -e ".[dev]"          # セットアップ
python -m pytest                 # テスト (ネットワーク不要、全て合成データ)
stock-screener screen            # 週次: ファンダスクリーニング → data/watchlist.csv + reports/watchlist.md
stock-screener screen --limit 15 # 動作確認用 (対象銘柄を制限)
stock-screener signals --no-notify  # 日次: シグナル判定 → data/signal_log.csv + reports/latest-signals.md + reports/performance.md
stock-screener backtest          # シグナル別の過去検証 → reports/backtest.md (--universe で全銘柄)
stock-screener portfolio         # 上位N銘柄の月次保有 vs 指数ETF (過去10年) → reports/portfolio-backtest.md
stock-screener model-portfolio   # ペーパー運用: 評価 + 月初の入れ替え → data/model_portfolio/ + reports/model-portfolio.md
```

## 大目的

投資リターンの最大化。評価の最上位は「指数ETFに勝てるか」(`reports/portfolio-backtest.md` の対指数 CAGR)。通知は当面しない (Markdown の蓄積のみ、Obsidian から読む)。

## アーキテクチャ (要点)

- 2段階方式: `screening/fundamental.py` が業種内パーセンタイルの重み付き平均でウォッチリストを作り、`signals/technical.py` がその銘柄だけに買いシグナル判定(ゴールデンクロス/52週高値ブレイク/押し目/出来高急増)を行う
- シグナルルールは `signals/technical.py` の `compute_signal_frame` に一本化(全営業日をベクトル計算)。`detect_signals`(日次)はその最終行、`backtest.py` は全行を使う。**ルールを変えるときはここだけ触る**
- 「新規」シグナル = 直前 `renotify_after_days` 営業日に同じシグナルが無かった(`first_of_episode`)。Slack通知・成績集計・バックテストのエントリー定義はすべてこれ
- `tracking.py`: `data/signal_log.csv` への追記(同じ判定日は置き換え=冪等)と N営業日後リターンの集計
- `portfolio.py`: 終値パネル → 月次リバランスの資産推移 (`simulate`)。モメンタム(`momentum_score` / `latest_momentum`)もここ。`screen` はこの `latest_momentum` を指標 `momentum_12_1` として合成スコアに入れる
- `model_portfolio.py`: ペーパー運用。`select_targets` (市場別上位N) → `rebalance` (等金額、コスト控除、継続銘柄は取得日引き継ぎ) → `value_of` (日次評価)。状態は `data/model_portfolio/*.csv`。月初判定は `should_rebalance` (前回入替の年月と比較)
- `data/prices.py` の `_remove_glitches`: yfinance 調整ミスによる一時的急落 (>50%、5営業日以内に回復) を前日値で補正。1306.T で実際に発生
- データ取得は `data/prices.py`(yf.download バッチ + 期間別Parquetキャッシュ)と `data/fundamentals.py`(Ticker.info を1件ずつ、ポーズ+リトライ付き)
- パラメータ(指標の重み・閾値・銘柄数・評価期間)はすべて `config.yaml`。ユニバースは `data/universe/*.csv`
- スコアリングはパーセンタイル(順位)ベースなので、yfinance の指標の単位揺れ(dividendYield が % か割合か等)の影響を受けない設計

## 現在の状態 (2026-10-20 時点の引き継ぎ)

- ブランチ `claude/improve-signal-tracking` に Phase 3(シグナル別バックテスト + ポートフォリオ・バックテスト + 成績追跡)と Actions 修正を実装。テスト63件パス。実データで screen / signals / backtest / portfolio / model-portfolio を実行済み
- 検証結果: テクニカル4シグナルは超過リターン ±1% 以内(価値なし → 参考情報に格下げ)。12-1ヶ月モメンタム上位15の月次保有は指数ETFとユニバース等金額を上回る(日本 CAGR +24% vs 指数 +12%、米国 +22% vs +15%)→ 合成スコアに重み 0.22 で採用
- 財務指標による選定は未検証。週次で `data/fundamentals/YYYY-MM-DD.csv` をコミットして蓄積中。1年分溜まったら「ウォッチリスト上位Nの月次保有」を `portfolio.py` の枠組みで検証する(Phase 4 の最優先)
- モデルポートフォリオ(ペーパー運用)を 2026-10-21 に開始(モメンタム戦略・合成スコア戦略、各30銘柄)。日次ワークフローが評価し、月初に入れ替える。テスト63件パス
- **GitHub Actions は 2026-07-11 以降すべて失敗していた**。原因は結果コミット手順で `git pull --rebase` を未コミット変更がある状態で実行していたこと(修正済み: 先に commit → pull --rebase → push、リトライ付き)。コミットが止まったため 60 日ルールでスケジュールも `disabled_inactivity` になっている
- **マージ後にやること**: Actions タブで Weekly Screening と Daily Signals を "Enable workflow" する(`gh workflow enable "Weekly Screening"` / `gh workflow enable "Daily Signals"`)。その後 Weekly Screening を手動実行して動作確認

### 既知の注意点

- yfinance のレート制限に当たったら `config.yaml` の `fetch.fundamentals_pause` を増やす(例: 0.5〜1.0)
- ローカルの `pytest` コマンドが別の Python を指している環境があるので `python -m pytest` を使う
- 上場廃止銘柄は Ticker.info が 404 になり時価総額フィルタで自動的に落ちる。ログに 404 が出たら `data/universe/*.csv` から削除する
- Phase 4(ルール見直し・第1段階の検証)は `reports/performance.md` の件数が溜まってから。設計は docs/system-design.md の ⑥ を参照
