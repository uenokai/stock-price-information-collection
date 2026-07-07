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


def now_jst() -> datetime:
    return datetime.now(tz=JST)


def write_watchlist_report(watchlist: pd.DataFrame, path: str | Path,
                           universe_size: int) -> Path:
    """週次スクリーニング結果 (ウォッチリスト) のレポートを書き出す。"""
    ts = now_jst()
    lines = [
        "# ウォッチリスト (週次ファンダメンタル・スクリーニング結果)",
        "",
        f"- 生成日時: {ts:%Y-%m-%d %H:%M} JST",
        f"- ユニバース: {universe_size} 銘柄 → ウォッチリスト: {len(watchlist)} 銘柄",
        "- スコアは業種内パーセンタイルの重み付き平均 (0〜1、高いほど良い)。",
        "  指標の詳細と重みは `config.yaml` を参照。",
        "",
        "| # | ティッカー | 銘柄名 | 市場 | 業種 | スコア | ROE | 売上成長 | PER | PBR | 指標数 |",
        "|--:|---|---|---|---|--:|--:|--:|--:|--:|--:|",
    ]
    for i, row in enumerate(watchlist.itertuples(index=False), start=1):
        lines.append(
            f"| {i} | {row.ticker} | {row.name} | {row.market} | {_str(getattr(row, 'sector', ''))} "
            f"| {row.score:.3f} | {_pct(getattr(row, 'roe', None))} "
            f"| {_pct(getattr(row, 'revenue_growth', None))} "
            f"| {_num(getattr(row, 'pe', None))} | {_num(getattr(row, 'pb', None))} "
            f"| {getattr(row, 'metric_coverage', '')} |"
        )
    lines += [
        "",
        "> 本レポートは機械的なスクリーニング結果であり、投資助言ではありません。",
        "",
    ]
    return _write(path, lines)


def write_signals_report(results: list[dict[str, Any]], path: str | Path,
                         watchlist_size: int) -> Path:
    """日次シグナル判定結果のレポートを書き出す。"""
    ts = now_jst()
    lines = [
        f"# 買いシグナル ({ts:%Y-%m-%d})",
        "",
        f"- 判定日時: {ts:%Y-%m-%d %H:%M} JST",
        f"- 監視対象: ウォッチリスト {watchlist_size} 銘柄",
        f"- シグナル成立: **{len(results)} 銘柄**",
        "",
    ]
    if not results:
        lines += ["本日成立した買いシグナルはありません。", ""]
    else:
        lines += [
            "| ティッカー | 銘柄名 | 市場 | シグナル | 終値 | RSI | 52週高値比 | 出来高倍率 |",
            "|---|---|---|---|--:|--:|--:|--:|",
        ]
        # シグナル数が多い銘柄 (複数同時成立 = 高信頼度) を上に
        for r in sorted(results, key=lambda r: len(r["signals"]), reverse=True):
            m = r["metrics"]
            labels = "、".join(SIGNAL_LABELS.get(s, s) for s in r["signals"])
            lines.append(
                f"| {r['ticker']} | {r['name']} | {r['market']} | {labels} "
                f"| {m['close']:,.1f} | {m['rsi']:.0f} | {m['pct_from_52w_high']:+.1%} "
                f"| {_num(m['volume_ratio'])}x |"
            )
        lines += [
            "",
            "## シグナルの見方",
            "",
        ]
        for name, label in SIGNAL_LABELS.items():
            lines.append(f"- **{label}** (`{name}`): {_SIGNAL_DESCRIPTIONS[name]}")
        lines.append("")
    lines += [
        "> 本レポートは機械的な判定結果であり、投資助言ではありません。",
        "",
    ]
    return _write(path, lines)


_SIGNAL_DESCRIPTIONS = {
    "golden_cross": "50日移動平均が200日移動平均を上抜け。長期トレンド転換の可能性。",
    "breakout_52w": "出来高を伴う52週高値の更新。上昇トレンド入りのサイン。",
    "pullback": "上昇トレンドを維持したままRSIが調整圏まで低下。押し目買いの候補。",
    "volume_surge": "平常時の数倍の出来高を伴う上昇。市場の注目度が変化した可能性。",
}


def build_slack_message(results: list[dict[str, Any]]) -> str:
    """Slack通知用のテキストを組み立てる。"""
    ts = now_jst()
    header = f":chart_with_upwards_trend: 買いシグナル {ts:%Y-%m-%d} — {len(results)} 銘柄"
    body_lines = []
    for r in sorted(results, key=lambda r: len(r["signals"]), reverse=True):
        labels = "、".join(SIGNAL_LABELS.get(s, s) for s in r["signals"])
        body_lines.append(f"• {r['ticker']} {r['name']}: {labels}")
    return header + "\n" + "\n".join(body_lines)


def _write(path: str | Path, lines: list[str]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _str(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return str(value)


def _num(value: Any, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:,.{digits}f}"


def _pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{value:.1%}"
