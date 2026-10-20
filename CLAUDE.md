# CLAUDE.md

中長期投資向けの銘柄発掘システム。設計は `docs/system-design.md`、使い方は `README.md` を参照。

## コマンド

```bash
pip install -e ".[dev]"          # セットアップ
python -m pytest                 # テスト (ネットワーク不要、全て合成データ)
stock-screener screen            # 週次: ファンダスクリーニング → data/watchlist.csv + reports/watchlist.md
stock-screener screen --limit 15 # 動作確認用 (対象銘柄を制限)
stock-screener signals --no-notify  # 日次: シグナル判定 → data/signal_log.csv + reports/latest-signals.md + reports/performance.md
stock-screener backtest          # ルールの過去検証 → reports/backtest.md (--universe で全銘柄, --period 10y 等)
```

## アーキテクチャ (要点)

- 2段階方式: `screening/fundamental.py` が業種内パーセンタイルの重み付き平均でウォッチリストを作り、`signals/technical.py` がその銘柄だけに買いシグナル判定(ゴールデンクロス/52週高値ブレイク/押し目/出来高急増)を行う
- シグナルルールは `signals/technical.py` の `compute_signal_frame` に一本化(全営業日をベクトル計算)。`detect_signals`(日次)はその最終行、`backtest.py` は全行を使う。**ルールを変えるときはここだけ触る**
- 「新規」シグナル = 直前 `renotify_after_days` 営業日に同じシグナルが無かった(`first_of_episode`)。Slack通知・成績集計・バックテストのエントリー定義はすべてこれ
- `tracking.py`: `data/signal_log.csv` への追記(同じ判定日は置き換え=冪等)と N営業日後リターンの集計
- データ取得は `data/prices.py`(yf.download バッチ + 期間別Parquetキャッシュ)と `data/fundamentals.py`(Ticker.info を1件ずつ、ポーズ+リトライ付き)
- パラメータ(指標の重み・閾値・銘柄数・評価期間)はすべて `config.yaml`。ユニバースは `data/universe/*.csv`
- スコアリングはパーセンタイル(順位)ベースなので、yfinance の指標の単位揺れ(dividendYield が % か割合か等)の影響を受けない設計

## 現在の状態 (2026-10-20 時点の引き継ぎ)

- ブランチ `claude/improve-signal-tracking` に Phase 3(バックテスト + シグナル成績追跡)と Actions 修正を実装。テスト37件パス。実データで screen / signals / backtest を実行済み
- **GitHub Actions は 2026-07-11 以降すべて失敗していた**。原因は結果コミット手順で `git pull --rebase` を未コミット変更がある状態で実行していたこと(修正済み: 先に commit → pull --rebase → push、リトライ付き)。コミットが止まったため 60 日ルールでスケジュールも `disabled_inactivity` になっている
- **マージ後にやること**: Actions タブで Weekly Screening と Daily Signals を "Enable workflow" する(`gh workflow enable "Weekly Screening"` / `gh workflow enable "Daily Signals"`)。その後 Weekly Screening を手動実行して動作確認

### 既知の注意点

- yfinance のレート制限に当たったら `config.yaml` の `fetch.fundamentals_pause` を増やす(例: 0.5〜1.0)
- ローカルの `pytest` コマンドが別の Python を指している環境があるので `python -m pytest` を使う
- 上場廃止銘柄は Ticker.info が 404 になり時価総額フィルタで自動的に落ちる。ログに 404 が出たら `data/universe/*.csv` から削除する
- Phase 4(ルール見直し・第1段階の検証)は `reports/performance.md` の件数が溜まってから。設計は docs/system-design.md の ⑥ を参照
