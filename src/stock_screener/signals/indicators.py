"""テクニカル指標の計算。"""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """単純移動平均。"""
    return series.rolling(window, min_periods=window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """RSI (Wilder方式の平滑化)。0〜100。

    エッジケース: 下落が一度もない期間は100、値動きが全くない期間は50とする。
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, min_periods=window, adjust=False).mean()

    result = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    result = result.where(avg_loss > 0, 100.0)
    result = result.where((avg_gain > 0) | (avg_loss > 0), 50.0)
    # ウォームアップ期間 (平均が未確定) は NaN のまま返す
    return result.where(avg_gain.notna() & avg_loss.notna())


def rolling_high(series: pd.Series, window: int) -> pd.Series:
    """当日を含まない過去N日の最高値 (高値ブレイク判定用)。"""
    return series.rolling(window, min_periods=window).max().shift(1)
