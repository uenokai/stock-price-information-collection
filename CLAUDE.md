# CLAUDE.md

中長期投資向けの銘柄発掘システム。設計は `docs/system-design.md`、使い方は `README.md` を参照。

## コマンド

```bash
pip install -e ".[dev]"          # セットアップ
pytest                           # テスト (ネットワーク不要、全て合成データ)
stock-screener screen            # 週次: ファンダスクリーニング → data/watchlist.csv + reports/watchlist.md
stock-screener screen --limit 15 # 動作確認用 (対象銘柄を制限)
stock-screener signals --no-notify  # 日次: シグナル判定 → reports/latest-signals.md
```

## アーキテクチャ (要点)

- 2段階方式: `screening/fundamental.py` が業種内パーセンタイルの重み付き平均でウォッチリストを作り、`signals/technical.py` がその銘柄だけに買いシグナル判定(ゴールデンクロス/52週高値ブレイク/押し目/出来高急増)を行う
- データ取得は `data/prices.py`(yf.download バッチ + Parquetキャッシュ)と `data/fundamentals.py`(Ticker.info を1件ずつ、ポーズ+リトライ付き)
- パラメータ(指標の重み・閾値・銘柄数)はすべて `config.yaml`。ユニバースは `data/universe/*.csv`
- スコアリングはパーセンタイル(順位)ベースなので、yfinance の指標の単位揺れ(dividendYield が % か割合か等)の影響を受けない設計

## 現在の状態 (2026-07-07 時点の引き継ぎ)

- ブランチ `claude/hello-cqwmyc` に Phase 1+2 実装済み・プッシュ済み。テスト23件パス。PR は未作成
- **実データでの動作確認が未実施**。前のセッションはネットワークポリシーで Yahoo Finance に接続できなかった(現在は環境設定で `query1/query2.finance.yahoo.com`, `fc.yahoo.com` を許可済みのはず)

### 次にやること (実データ検証)

1. 接続確認: `curl -sS -o /dev/null -w "%{http_code}" "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d"` → 200 が返ること
2. `stock-screener screen --limit 15` で小さく実行し、財務データが取れているか・スコアが出るかを確認
3. 問題なければ `stock-screener screen`(全221銘柄、5分前後)→ `reports/watchlist.md` の内容を目視確認
4. `stock-screener signals --no-notify` → `reports/latest-signals.md` を確認
5. yfinance の実データでフィールド欠損・仕様差異があれば `data/fundamentals.py` の `INFO_FIELDS` を修正
6. 生成された watchlist / レポートをコミットしてプッシュ(ブランチは `claude/hello-cqwmyc` のまま)

### 既知の注意点

- yfinance のレート制限に当たったら `config.yaml` の `fetch.fundamentals_pause` を増やす(例: 0.5〜1.0)
- Phase 3(バックテスト)は未着手。設計は docs/system-design.md の ⑥ を参照
