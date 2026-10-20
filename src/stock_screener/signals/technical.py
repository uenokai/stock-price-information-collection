"""テクニカル・タイミング判定 (第2段階)。

ウォッチリスト銘柄の日足に対して、中長期投資に適した買いシグナルを判定する。
シグナルは「良い銘柄を高値掴みしない/勢いに乗る」ための補助であり、
複数シグナルの同時成立を高信頼度として扱う。

ルールは `compute_signal_frame` に一本化してある (全営業日分をベクトル計算)。
日次判定 (`detect_signals`) はその最終行を、バックテストは全行を使うので、
「本番で鳴るシグナル」と「バックテストで検証するシグナル」が必ず一致する。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
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
SIGNAL_NAMES = list(SIGNAL_LABELS)

# 200日SMA + 52週高値の計算に必要な最低営業日数
MIN_HISTORY_DAYS = 260

# compute_signal_frame が返す指標カラム
INDICATOR_COLUMNS = ["close", "sma50", "sma200", "rsi", "volume_ratio",
                     "high_52w", "daily_return", "uptrend", "valid"]


def compute_signal_frame(df: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """全営業日についてシグナル成立の真偽と指標を計算する。

    Args:
        df: index=日付 (昇順), columns=[open, high, low, close, volume]
        params: config.yaml の signals セクション

    Returns:
        index=日付, columns=SIGNAL_NAMES (bool) + INDICATOR_COLUMNS。
        指標が未確定の日 (valid=False) ではシグナルは全て False。
        履歴が MIN_HISTORY_DAYS 未満なら空の DataFrame。
    """
    if df is None or len(df) < MIN_HISTORY_DAYS:
        return pd.DataFrame(columns=SIGNAL_NAMES + INDICATOR_COLUMNS)

    close = df["close"].astype(float)
    volume = df["volume"].fillna(0).astype(float)

    sma50 = sma(close, 50)
    sma200 = sma(close, 200)
    rsi14 = rsi(close, 14)
    vol_sma20 = sma(volume, 20)
    high_52w = rolling_high(close, 252)

    volume_ratio = volume.div(vol_sma20).where(vol_sma20 > 0)
    daily_return = close.pct_change()
    uptrend = (close > sma200) & (sma50 > sma200)
    valid = sma50.notna() & sma200.notna() & rsi14.notna() & vol_sma20.notna() & high_52w.notna()

    # ゴールデンクロス: 直近N営業日以内 (当日含む) に50日SMAが200日SMAを上抜け
    lookback = int(params.get("golden_cross_lookback", 5))
    cross_up = (sma50 > sma200) & (sma50.shift(1) <= sma200.shift(1))
    golden_cross = cross_up.astype(float).rolling(lookback, min_periods=1).max() > 0

    # 52週高値ブレイク: 出来高を伴う新高値更新
    volume_mult = float(params.get("breakout_volume_mult", 1.5))
    breakout = (close >= high_52w) & (volume_ratio >= volume_mult)

    # 押し目: 上昇トレンドを維持したままRSIが調整圏まで低下
    rsi_min = float(params.get("pullback_rsi_min", 30))
    rsi_max = float(params.get("pullback_rsi_max", 45))
    pullback = uptrend & (rsi14 >= rsi_min) & (rsi14 <= rsi_max)

    # 出来高急増: 平常時の数倍の出来高 + 明確な上昇 (注目度の変化を捉える)
    surge_mult = float(params.get("volume_surge_mult", 3.0))
    surge_min_return = float(params.get("volume_surge_min_return", 0.02))
    volume_surge = (volume_ratio >= surge_mult) & (daily_return >= surge_min_return)

    frame = pd.DataFrame({
        "golden_cross": golden_cross & valid,
        "breakout_52w": breakout & valid,
        "pullback": pullback & valid,
        "volume_surge": volume_surge & valid,
        "close": close,
        "sma50": sma50,
        "sma200": sma200,
        "rsi": rsi14,
        "volume_ratio": volume_ratio,
        "high_52w": high_52w,
        "daily_return": daily_return,
        "uptrend": uptrend,
        "valid": valid,
    }, index=df.index)
    for name in SIGNAL_NAMES:
        frame[name] = frame[name].fillna(False).astype(bool)
    return frame


def first_of_episode(flag: pd.Series, gap: int) -> pd.Series:
    """「新規」シグナルの判定: 成立日のうち、直前 gap 営業日に同じシグナルが無かった日だけ True。

    押し目のように連日成立するシグナルを1回のエントリーとして数えるために使う。
    gap=0 なら毎日を新規扱い。
    """
    flag = flag.fillna(False).astype(bool)
    if gap <= 0:
        return flag
    recent = flag.astype(float).shift(1).rolling(gap, min_periods=1).max().fillna(0) > 0
    return flag & ~recent


def detect_signals(
    df: pd.DataFrame, params: dict[str, Any]
) -> tuple[list[str], dict[str, float]]:
    """1銘柄の日足OHLCVから、当日成立している買いシグナルを判定する。

    Returns:
        (成立シグナル名のリスト, 判定時点の指標スナップショット)
        履歴不足・指標未確定の場合は ([], {})
    """
    frame = compute_signal_frame(df, params)
    if frame.empty or not bool(frame["valid"].iloc[-1]):
        return [], {}

    last = frame.iloc[-1]
    signals = [name for name in SIGNAL_NAMES if bool(last[name])]
    volume_ratio = float(last["volume_ratio"]) if pd.notna(last["volume_ratio"]) else float("nan")
    metrics = {
        "close": float(last["close"]),
        "rsi": round(float(last["rsi"]), 1),
        "volume_ratio": round(volume_ratio, 2) if not math.isnan(volume_ratio) else float("nan"),
        "pct_from_52w_high": round(float(last["close"]) / float(last["high_52w"]) - 1.0, 4),
        "daily_return": round(float(last["daily_return"]), 4),
        "uptrend": float(bool(last["uptrend"])),
    }
    return signals, metrics


def classify_new_signals(
    df: pd.DataFrame, signals: list[str], params: dict[str, Any]
) -> dict[str, bool]:
    """当日成立したシグナルそれぞれについて、新規 (True) か継続 (False) かを返す。

    新規 = 同じシグナルが直前 `renotify_after_days` 営業日に成立していなかった。
    """
    gap = int(params.get("renotify_after_days", 10))
    if not signals:
        return {}
    frame = compute_signal_frame(df, params)
    if frame.empty:
        return {s: True for s in signals}
    return {s: bool(first_of_episode(frame[s], gap).iloc[-1]) for s in signals}


def scan_watchlist(
    prices: dict[str, pd.DataFrame],
    watchlist: pd.DataFrame,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """ウォッチリスト全銘柄のシグナルを判定し、成立した銘柄だけ返す。

    各結果には signals (成立シグナル名), new_signals (うち新規のもの), metrics,
    score (ファンダスコア、あれば), bar_date (判定に使った最終足の日付) を含む。
    """
    results = []
    skipped = 0
    for row in watchlist.itertuples(index=False):
        df = prices.get(row.ticker)
        if df is None:
            skipped += 1
            continue
        signals, metrics = detect_signals(df, params)
        if signals:
            is_new = classify_new_signals(df, signals, params)
            score = getattr(row, "score", float("nan"))
            results.append({
                "ticker": row.ticker,
                "name": getattr(row, "name", row.ticker),
                "market": getattr(row, "market", ""),
                "signals": signals,
                "new_signals": [s for s in signals if is_new.get(s, True)],
                "metrics": metrics,
                "score": float(score) if score is not None and not _isnan(score) else float("nan"),
                "bar_date": pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d"),
            })
    if skipped:
        logger.warning("株価データが取得できずスキップした銘柄: %d 件", skipped)
    n_new = sum(1 for r in results if r["new_signals"])
    logger.info("シグナル判定完了: %d 銘柄中 %d 銘柄でシグナル成立 (うち新規 %d 銘柄)",
                len(watchlist), len(results), n_new)
    return results


def _isnan(value: Any) -> bool:
    try:
        return bool(np.isnan(float(value)))
    except (TypeError, ValueError):
        return True
