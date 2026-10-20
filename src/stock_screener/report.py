"""Markdownレポートの生成。

レポートはリポジトリにコミットされるので、GitHub上でいつでも閲覧できる。
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .signals.technical import SIGNAL_LABELS

JST = ZoneInfo("Asia/Tokyo")

DISCLAIMER = "> 本レポートは機械的な判定結果であり、投資助言ではありません。"


def now_jst() -> datetime:
    return datetime.now(tz=JST)


# ---------------------------------------------------------------------------
# 週次: ウォッチリスト
# ---------------------------------------------------------------------------

def write_watchlist_report(
    watchlist: pd.DataFrame,
    path: str | Path,
    universe_size: int,
    previous: pd.DataFrame | None = None,
) -> Path:
    """週次スクリーニング結果 (ウォッチリスト) のレポートを書き出す。

    previous (前回のウォッチリスト) を渡すと、入れ替わった銘柄を併記する。
    """
    ts = now_jst()
    lines = [
        "# ウォッチリスト (週次ファンダメンタル・スクリーニング結果)",
        "",
        f"- 生成日時: {ts:%Y-%m-%d %H:%M} JST",
        f"- ユニバース: {universe_size} 銘柄 → ウォッチリスト: {len(watchlist)} 銘柄",
        "- スコアは業種内パーセンタイルの重み付き平均 (0〜1、高いほど良い)。",
        "  指標の詳細と重みは `config.yaml` を参照。",
        "",
    ]
    lines += _watchlist_diff_lines(watchlist, previous)
    lines += [
        "| # | ティッカー | 銘柄名 | 市場 | 業種 | スコア | ROE | 売上成長 | PER | PBR | モメンタム | 指標数 |",
        "|--:|---|---|---|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    prev_tickers = set(previous["ticker"]) if previous is not None and "ticker" in previous else set()
    for i, row in enumerate(watchlist.itertuples(index=False), start=1):
        mark = " 🆕" if prev_tickers and row.ticker not in prev_tickers else ""
        lines.append(
            f"| {i} | {row.ticker}{mark} | {row.name} | {row.market} | {_str(getattr(row, 'sector', ''))} "
            f"| {row.score:.3f} | {_pct(getattr(row, 'roe', None))} "
            f"| {_pct(getattr(row, 'revenue_growth', None))} "
            f"| {_num(getattr(row, 'pe', None))} | {_num(getattr(row, 'pb', None))} "
            f"| {_pct_signed(getattr(row, 'momentum_12_1', None))} "
            f"| {getattr(row, 'metric_coverage', '')} |"
        )
    lines += ["", DISCLAIMER, ""]
    return _write(path, lines)


def _watchlist_diff_lines(watchlist: pd.DataFrame, previous: pd.DataFrame | None) -> list[str]:
    if previous is None or previous.empty or "ticker" not in previous.columns:
        return []
    now = watchlist.set_index("ticker")
    before = previous.set_index("ticker")
    added = [t for t in now.index if t not in before.index]
    removed = [t for t in before.index if t not in now.index]
    name_of = lambda df, t: _str(df.loc[t].get("name", t)) if "name" in df.columns else t  # noqa: E731
    lines = [f"## 前回からの入れ替え (新規 {len(added)} / 除外 {len(removed)})", ""]
    if added:
        lines.append("- 新規追加: " + "、".join(f"{t} {name_of(now, t)}" for t in added))
    if removed:
        lines.append("- 除外: " + "、".join(f"{t} {name_of(before, t)}" for t in removed))
    if not added and not removed:
        lines.append("- 入れ替えなし")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 日次: 買いシグナル
# ---------------------------------------------------------------------------

def sort_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """新規あり → シグナル数が多い → ファンダスコアが高い、の順。"""
    def key(r):
        score = r.get("score", float("nan"))
        score = -1.0 if score is None or (isinstance(score, float) and math.isnan(score)) else score
        return (len(r.get("new_signals", r["signals"])) > 0, len(r["signals"]), score)
    return sorted(results, key=key, reverse=True)


def _signal_labels(r: dict[str, Any]) -> str:
    new_set = set(r.get("new_signals", r["signals"]))
    parts = []
    for s in r["signals"]:
        label = SIGNAL_LABELS.get(s, s)
        parts.append(f"🆕{label}" if s in new_set else label)
    return "、".join(parts)


def write_signals_report(results: list[dict[str, Any]], path: str | Path,
                         watchlist_size: int) -> Path:
    """日次シグナル判定結果のレポートを書き出す。"""
    ts = now_jst()
    n_new = sum(1 for r in results if r.get("new_signals", r["signals"]))
    lines = [
        f"# 買いシグナル ({ts:%Y-%m-%d})",
        "",
        f"- 判定日時: {ts:%Y-%m-%d %H:%M} JST",
        f"- 監視対象: ウォッチリスト {watchlist_size} 銘柄",
        f"- シグナル成立: **{len(results)} 銘柄** (うち新規 🆕 {n_new} 銘柄)",
        "- 🆕 = そのシグナルが直近の営業日に成立していなかった (= 今日初めて点灯)。無印は継続中。",
        "- バックテスト (`reports/backtest.md`) ではこれらのシグナルに買う日を決める価値はほぼ無かった。"
        "参考情報として記録・追跡している。",
        "",
    ]
    if not results:
        lines += ["本日成立した買いシグナルはありません。", ""]
    else:
        lines += [
            "| ティッカー | 銘柄名 | 市場 | シグナル | ファンダ | 終値 | RSI | 52週高値比 | 出来高倍率 |",
            "|---|---|---|---|--:|--:|--:|--:|--:|",
        ]
        for r in sort_results(results):
            m = r["metrics"]
            lines.append(
                f"| {r['ticker']} | {r['name']} | {r['market']} | {_signal_labels(r)} "
                f"| {_num(r.get('score'), 3)} "
                f"| {m['close']:,.1f} | {m['rsi']:.0f} | {m['pct_from_52w_high']:+.1%} "
                f"| {_num(m['volume_ratio'])}x |"
            )
        lines += ["", "## シグナルの見方", ""]
        for name, label in SIGNAL_LABELS.items():
            lines.append(f"- **{label}** (`{name}`): {_SIGNAL_DESCRIPTIONS[name]}")
        lines.append("")
    lines += [
        "過去シグナルの成績は `reports/performance.md`、ルールの過去検証は `reports/backtest.md` を参照。",
        "",
        DISCLAIMER,
        "",
    ]
    return _write(path, lines)


_SIGNAL_DESCRIPTIONS = {
    "golden_cross": "50日移動平均が200日移動平均を上抜け。長期トレンド転換の可能性。",
    "breakout_52w": "出来高を伴う52週高値の更新。上昇トレンド入りのサイン。",
    "pullback": "上昇トレンドを維持したままRSIが調整圏まで低下。押し目買いの候補。",
    "volume_surge": "平常時の数倍の出来高を伴う上昇。市場の注目度が変化した可能性。",
}


def build_slack_message(results: list[dict[str, Any]], only_new: bool = True) -> str:
    """Slack通知用のテキストを組み立てる。only_new なら新規シグナルのある銘柄だけ。"""
    ts = now_jst()
    targets = [r for r in results if r.get("new_signals", r["signals"])] if only_new else results
    n_cont = len(results) - len(targets)
    header = f":chart_with_upwards_trend: 買いシグナル {ts:%Y-%m-%d} — 新規 {len(targets)} 銘柄"
    if n_cont:
        header += f" (継続中 {n_cont} 銘柄はレポート参照)"
    body_lines = []
    for r in sort_results(targets):
        labels = _signal_labels(r)
        score = _num(r.get("score"), 2)
        body_lines.append(f"• {r['ticker']} {r['name']}: {labels} (ファンダ {score})")
    return header + "\n" + "\n".join(body_lines)


# ---------------------------------------------------------------------------
# 成績表 (performance / backtest 共通)
# ---------------------------------------------------------------------------

_GROUP_LABELS = {
    "all": "全シグナル合計",
    "baseline": "ベースライン (全営業日に買った場合)",
    "uptrend_days": "上昇トレンド中の全営業日 (株価>200日線 かつ 50日線>200日線)",
    "combo_2plus": "複数シグナル同時成立 (2つ以上)",
}


def _group_label(name: Any) -> str:
    return _GROUP_LABELS.get(str(name), SIGNAL_LABELS.get(str(name), str(name)))


def _stats_table(summary: pd.DataFrame, horizons: list[int], group_col: str,
                 show_excess: bool = False) -> list[str]:
    """summarize_returns の結果を Markdown 表にする。"""
    if summary.empty:
        return ["(データなし)", ""]
    header = f"| {'区分' if group_col != 'market' else '市場'} | 件数 |"
    sep = "|---|--:|"
    for h in horizons:
        header += f" +{h}日 勝率 | +{h}日 平均 | +{h}日 中央値 |"
        sep += "--:|--:|--:|"
        if show_excess:
            header += f" +{h}日 超過 |"
            sep += "--:|"
    lines = [header, sep]
    for row in summary.itertuples(index=False):
        d = row._asdict()
        line = f"| {_group_label(d[group_col])} | {int(d['entries'])} |"
        for h in horizons:
            n = int(d.get(f"n_{h}", 0))
            if n == 0:
                line += " — | — | — |"
                if show_excess:
                    line += " — |"
                continue
            line += (f" {d[f'win_{h}']:.0%} ({n}) | {d[f'mean_{h}']:+.1%} "
                     f"| {d[f'median_{h}']:+.1%} |")
            if show_excess:
                ex = d.get(f"excess_{h}")
                line += f" {ex:+.1%} |" if ex is not None and not _nan(ex) else " — |"
        lines.append(line)
    lines.append("")
    return lines


def write_performance_report(
    evaluated: pd.DataFrame,
    summary: pd.DataFrame,
    horizons: list[int],
    path: str | Path,
    recent_limit: int = 40,
) -> Path:
    """台帳に記録したシグナルの事後成績レポートを書き出す。"""
    ts = now_jst()
    entries = evaluated[evaluated["is_new"]] if "is_new" in evaluated.columns else evaluated
    first = entries["date"].min() if not entries.empty else "—"
    lines = [
        "# シグナル成績表 (実際に出したシグナルの追跡)",
        "",
        f"- 更新日時: {ts:%Y-%m-%d %H:%M} JST",
        f"- 集計対象: `data/signal_log.csv` の新規シグナル {len(entries)} 件 (記録開始: {first})",
        "- 勝率 = N営業日後の終値がエントリー時 (判定に使った終値) を上回った割合。括弧内は評価済み件数。",
        "- 日数が経過していないシグナルは「—」。件数が十分 (目安30件以上) になるまで数字は参考程度。",
        "",
        "## シグナル種別ごとの成績",
        "",
    ]
    lines += _stats_table(summary, horizons, "signal")
    lines += ["## 直近のシグナル (新規のみ、新しい順)", ""]
    if entries.empty:
        lines += ["まだ記録がありません。", ""]
    else:
        head = "| 判定日 | ティッカー | 銘柄名 | シグナル | 終値 | 経過日 | 現在 |"
        sep = "|---|---|---|---|--:|--:|--:|"
        for h in horizons:
            head += f" +{h}日 |"
            sep += "--:|"
        lines += [head, sep]
        recent = entries.sort_values(["date", "ticker"], ascending=[False, True]).head(recent_limit)
        for row in recent.itertuples(index=False):
            d = row._asdict()
            line = (f"| {d['date']} | {d['ticker']} | {_str(d.get('name'))} "
                    f"| {_group_label(d['signal'])} | {_num(d.get('close'))} "
                    f"| {_num(d.get('days_elapsed'), 0)} | {_pct_signed(d.get('ret_now'))} |")
            for h in horizons:
                line += f" {_pct_signed(d.get(f'ret_{h}'))} |"
            lines.append(line)
        lines.append("")
    lines += [DISCLAIMER, ""]
    return _write(path, lines)


def write_backtest_report(
    result: dict[str, Any],
    horizons: list[int],
    path: str | Path,
    period: str,
    gap: int,
    scope: str,
    meta: pd.DataFrame | None = None,
) -> Path:
    """バックテスト結果のレポートを書き出す。"""
    ts = now_jst()
    entries = result["entries"]
    core = entries[entries["signal"] != "combo_2plus"] if not entries.empty else entries
    lines = [
        "# バックテスト (シグナルルールの過去検証)",
        "",
        f"- 実行日時: {ts:%Y-%m-%d %H:%M} JST",
        f"- 対象: {scope} {result['n_tickers']} 銘柄 / 株価期間 {period} "
        f"(判定可能な営業日 延べ {result['n_days']:,} 日)",
        f"- エントリー定義: シグナル成立日のうち、直前 {gap} 営業日に同じシグナルが無かった日 "
        f"(本番の「新規」通知と同じ定義)。エントリー総数 {len(core)} 件",
        "- 「超過」= シグナル日の平均リターン − ベースライン (同じ銘柄群の全営業日に買った場合の平均)。"
        " ここがプラスでなければ、そのシグナルにタイミングとしての価値は無い。",
        "",
        "## シグナル種別ごとの成績",
        "",
    ]
    lines += _stats_table(result["summary"], horizons, "signal", show_excess=True)
    by_market = result.get("by_market")
    if by_market is not None and not by_market.empty:
        lines += ["## 市場別 (全シグナル)", ""]
        lines += _stats_table(by_market, horizons, "market")
    lines += [
        "## 注意",
        "",
        "- 対象銘柄は**現在の**財務指標で選ばれているため、過去の値動きに当てはめると"
        "生存バイアス・先読みバイアスが入る。ここで見るのは第2段階 (タイミング判定) のルールの良し悪し。",
        "- 手数料・スリッページ・配当は考慮していない。終値で買い、N営業日後の終値で評価した単純比較。",
        "- 件数が少ない区分 (目安30件未満) の数字は偶然の影響が大きい。",
        "",
        DISCLAIMER,
        "",
    ]
    return _write(path, lines)


# ---------------------------------------------------------------------------
# ポートフォリオ・バックテスト
# ---------------------------------------------------------------------------

_STRATEGY_LABELS = {
    "index": "指数ETF (買って持ち続ける)",
    "universe_ew": "ユニバース全銘柄 等金額 (月次リバランス)",
    "momentum_top": "モメンタム上位N 等金額",
    "momentum_trend": "モメンタム上位N + トレンドフィルタ",
    "trend_only": "トレンドフィルタのみ (200日線より上の全銘柄 等金額)",
}
_STRATEGY_SHORT = {
    "index": "指数ETF",
    "universe_ew": "全銘柄等金額",
    "momentum_top": "モメンタム",
    "momentum_trend": "モメンタム+トレンド",
    "trend_only": "トレンドのみ",
}


def write_portfolio_report(results: dict[str, Any], path: str | Path, period: str,
                           cfg: dict[str, Any]) -> Path:
    """ポートフォリオ・バックテストのレポートを書き出す。"""
    ts = now_jst()
    lines = [
        "# ポートフォリオ・バックテスト (指数に勝てるかの検証)",
        "",
        f"- 実行日時: {ts:%Y-%m-%d %H:%M} JST / 株価期間 {period}",
        f"- 戦略: 毎月初の営業日に基準で上位 {cfg.get('top_n_per_market', 15)} 銘柄を選び等金額で保有。"
        f" 入れ替えコスト往復 {float(cfg.get('cost_rate', 0.002)):.2%} を控除。配当は株価に反映済み (yfinance 調整後終値)",
        "- 「対指数」= その戦略の CAGR − 指数ETFの CAGR。**ここがプラスでなければ指数ETFを買う方が良い**",
        "- モメンタム = 12ヶ月前から1ヶ月前までの騰落率 (直近1ヶ月を除く標準的な定義)",
        "",
    ]
    if not results:
        lines += ["価格データが不足しているため結果がありません。", ""]
    for market, res in results.items():
        strategies = res["strategies"]
        idx_cagr = strategies["index"]["metrics"].get("cagr", float("nan"))
        lines += [
            f"## {market} ({res['n_tickers']} 銘柄, ベンチマーク {res['benchmark']}, "
            f"{res['start']:%Y-%m-%d} 〜 {res['end']:%Y-%m-%d})",
            "",
            "| 戦略 | 累積 | CAGR | 対指数 | 年率ボラ | 最大DD | シャープ | 平均入替率/月 |",
            "|---|--:|--:|--:|--:|--:|--:|--:|",
        ]
        for name, s_res in strategies.items():
            m = s_res["metrics"]
            excess = m["cagr"] - idx_cagr if not _nan(m.get("cagr")) and not _nan(idx_cagr) else float("nan")
            lines.append(
                f"| {_STRATEGY_LABELS.get(name, name)} | {_pct_signed(m.get('total'))} "
                f"| {_pct_signed(m.get('cagr'))} | {_pct_signed(excess) if name != 'index' else '—'} "
                f"| {_pct(m.get('vol'))} | {_pct_signed(m.get('max_dd'))} | {_num(m.get('sharpe'), 2)} "
                f"| {_pct(s_res.get('turnover'))} |"
            )
        lines.append("")
        yearly = res.get("yearly")
        if yearly is not None and not yearly.empty:
            cols = list(yearly.columns)
            lines += ["暦年リターン:", "",
                      "| 年 | " + " | ".join(_STRATEGY_SHORT.get(c, c) for c in cols) + " |",
                      "|---|" + "--:|" * len(cols)]
            first_year, last_year = yearly.index[0], yearly.index[-1]
            for year, row in yearly.iterrows():
                label = str(year)
                if year == first_year and (res["start"].month, res["start"].day) != (1, 1):
                    label += f" ({res['start']:%m/%d}〜)"
                if year == last_year and (res["end"].month, res["end"].day) < (12, 28):
                    label += f" (〜{res['end']:%m/%d})"
                lines.append(f"| {label} | " + " | ".join(_pct_signed(row[c]) for c in cols) + " |")
            lines.append("")
    lines += [
        "## 読み方と限界",
        "",
        "- 「ユニバース全銘柄 等金額」が指数を上回る分は、ユニバースの選び方 (現在の大型株を後から選んでいる = 生存バイアス) "
        "による上乗せを含む。戦略の実力は **「ユニバース全銘柄 等金額」との差** で見るのが公平。",
        "- 財務指標による選定 (週次スクリーニング) は過去時点の値が無いため、ここでは検証していない。"
        "`data/fundamentals/` の週次スナップショットが1年以上溜まった時点で同じ枠組みで検証する。",
        "- 為替は考慮していない (各市場を現地通貨で評価)。税金は考慮していない。",
        "",
        DISCLAIMER,
        "",
    ]
    return _write(path, lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write(path: str | Path, lines: list[str]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _nan(value: Any) -> bool:
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return True


def _str(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return str(value)


def _num(value: Any, digits: int = 1) -> str:
    if value is None or _nan(value):
        return "—"
    return f"{float(value):,.{digits}f}"


def _pct(value: Any) -> str:
    if value is None or _nan(value):
        return "—"
    return f"{float(value):.1%}"


def _pct_signed(value: Any) -> str:
    if value is None or _nan(value):
        return "—"
    return f"{float(value):+.1%}"
