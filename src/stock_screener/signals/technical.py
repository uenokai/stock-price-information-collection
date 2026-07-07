"""テクニカル・タイミング判定 (第2段階)。

ウォッチリスト銘柄の日足に対して、中長期投資に適した買いシグナルを判定する。
シグナルは「良い銘柄を高値掴みしない/勢いに乗る」ための補助であり、
複数シグナルの同時成立を高信頼度として扱う。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd

from .indicators import rolling_high, rsi, sma

logger = logging.getLogger(__name__)

# シグナル名 → レポート表示用ラベル
SIGNAL_LABELS = {
    "golden_cross": "ゴールデンクロス (50日/200日)",
    "breakout_52w": "52週高値ブレイク",
    "pullback": "押し目 (上昇トレンド中の調整)",
    "volume_surge": "出来高急増",
}

# 200日SMA + 52週高値の計算に必要な最低営業日数
MIN_HISTORY_DAYS = 260


def detect_signals(
    df: pd.DataFrame, params: dict[str, Any]
) -> tuple[list[str], dict[str, float]]:
    """1銘柄の日足OHLCVから、当日成立している買いシグナルを判定する。

    Args:
        df: index=日付, columns=[open, high, low, close, volume] (日付昇順)
        params: config.yaml の signals セクション

    Returns:
        (成立シグナル名のリスト, 判定時点の指標スナップショット)
        履歴不足・指標未確定の場合は ([], {})
    """
    if df is None or len(df) < MIN_HISTORY_DAYS:
        return [], {}

    close = df["close"]
    volume = df["volume"].fillna(0)

    sma50 = sma(close, 50)
    sma200 = sma(close, 200)
    rsi14 = rsi(close, 14)
    vol_sma20 = sma(volume, 20)
    high_52w = rolling_high(close, 252)

    last_values = (sma50.iloc[-1], sma200.iloc[-1], rsi14.iloc[-1],
                   vol_sma20.iloc[-1], high_52w.iloc[-1])
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in last_values):
        return [], {}

    last_close = float(close.iloc[-1])
    last_volume = float(volume.iloc[-1])
    volume_ratio = last_volume / last_values[3] if last_values[3] > 0 else float("nan")
    daily_return = float(close.pct_change().iloc[-1])
    uptrend = last_close > sma200.iloc[-1] and sma50.iloc[-1] > sma200.iloc[-1]

    signals: list[str] = []

    # ゴールデンクロス: 直近N営業日以内に50日SMAが200日SMAを上抜け
    lookback = int(params.get("golden_cross_lookback", 5))
    cross_up = (sma50 > sma200) & (sma50.shift(1) <= sma200.shift(1))
    if bool(cross_up.iloc[-lookback:].any()):
        signals.append("golden_cross")

    # 52週高値ブレイク: 出来高を伴う新高値更新
    volume_mult = float(params.get("breakout_volume_mult", 1.5))
    if last_close >= high_52w.iloc[-1] and volume_ratio >= volume_mult:
        signals.append("breakout_52w")

    # 押し目: 上昇トレンドを維持したままRSIが調整圏まで低下
    rsi_min = float(params.get("pullback_rsi_min", 30))
    rsi_max = float(params.get("pullback_rsi_max", 45))
    if uptrend and rsi_min <= rsi14.iloc[-1] <= rsi_max:
        signals.append("pullback")

    # 出来高急増: 平常時の数倍の出来高 + 明確な上昇 (注目度の変化を捉える)
    surge_mult = float(params.get("volume_surge_mult", 3.0))
    surge_min_return = float(params.get("volume_surge_min_return", 0.02))
    if volume_ratio >= surge_mult and daily_return >= surge_min_return:
        signals.append("volume_surge")

    metrics = {
        "close": last_close,
        "rsi": round(float(rsi14.iloc[-1]), 1),
        "volume_ratio": round(volume_ratio, 2) if not math.isnan(volume_ratio) else float("nan"),
        "pct_from_52w_high": round(last_close / float(high_52w.iloc[-1]) - 1.0, 4),
        "daily_return": round(daily_return, 4),
        "uptrend": float(uptrend),
    }
    return signals, metrics


def scan_watchlist(
    prices: dict[str, pd.DataFrame],
    watchlist: pd.DataFrame,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """ウォッチリスト全銘柄のシグナルを判定し、成立した銘柄だけ返す。"""
    results = []
    skipped = 0
    for row in watchlist.itertuples(index=False):
        df = prices.get(row.ticker)
        if df is None:
            skipped += 1
            continue
        signals, metrics = detect_signals(df, params)
        if signals:
            results.append({
                "ticker": row.ticker,
                "name": getattr(row, "name", row.ticker),
                "market": getattr(row, "market", ""),
                "signals": signals,
                "metrics": metrics,
            })
    if skipped:
        logger.warning("株価データが取得できずスキップした銘柄: %d 件", skipped)
    logger.info("シグナル判定完了: %d 銘柄中 %d 銘柄でシグナル成立",
                len(watchlist), len(results))
    return results
