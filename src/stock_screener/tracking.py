"""シグナル台帳 (signal log) と事後成績の追跡。

日次判定で成立したシグナルを `data/signal_log.csv` に追記し、
後日その銘柄がどうなったか (N営業日後のリターン) を集計する。
「このルールは実際に機能しているか」を数字で確認するためのフィードバックループ。

台帳の行 = (判定日, 銘柄, シグナル) の組。is_new=True の行だけを
「エントリー」として成績集計に使う (連日成立する押し目を重複して数えないため)。
一度確定した N営業日後リターン (ret_N 列) は台帳に書き戻して保存する。
株価の取得期間 (2年) より古いエントリーでも成績が消えないようにするため。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

LOG_COLUMNS = [
    "date", "bar_date", "ticker", "name", "market", "signal", "is_new",
    "close", "score", "rsi", "volume_ratio", "pct_from_52w_high",
]
DEFAULT_HORIZONS = [20, 60, 120]


def results_to_log_rows(results: list[dict[str, Any]], run_date: str) -> pd.DataFrame:
    """scan_watchlist の結果を台帳の行 (1シグナル=1行) に変換する。"""
    rows = []
    for r in results:
        m = r.get("metrics", {})
        new_set = set(r.get("new_signals", r["signals"]))
        for signal in r["signals"]:
            rows.append({
                "date": run_date,
                "bar_date": r.get("bar_date", run_date),
                "ticker": r["ticker"],
                "name": r.get("name", r["ticker"]),
                "market": r.get("market", ""),
                "signal": signal,
                "is_new": signal in new_set,
                "close": m.get("close", np.nan),
                "score": r.get("score", np.nan),
                "rsi": m.get("rsi", np.nan),
                "volume_ratio": m.get("volume_ratio", np.nan),
                "pct_from_52w_high": m.get("pct_from_52w_high", np.nan),
            })
    return pd.DataFrame(rows, columns=LOG_COLUMNS)


def load_signal_log(path: str | Path | None) -> pd.DataFrame:
    """台帳を読み込む。無ければ空の DataFrame。確定済みの ret_N 列があれば保持する。"""
    if path is None or not Path(path).exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    df = pd.read_csv(path, dtype={"ticker": str, "signal": str})
    for col in LOG_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df["is_new"] = df["is_new"].astype(str).str.lower().isin(["true", "1"])
    ret_cols = [c for c in df.columns if c.startswith("ret_") and c != "ret_now"]
    return df[LOG_COLUMNS + ret_cols]


def save_signal_log(path: str | Path, log: pd.DataFrame) -> None:
    """台帳を保存する。識別列 + 確定済み ret_N 列のみ (ret_now 等の一時列は除く)。"""
    ret_cols = [c for c in log.columns if c.startswith("ret_") and c != "ret_now"]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    log[LOG_COLUMNS + ret_cols].to_csv(path, index=False)


def update_signal_log(path: str | Path, new_rows: pd.DataFrame) -> pd.DataFrame:
    """台帳に当日分を追記して保存する。同じ判定日の行は置き換える (再実行しても重複しない)。"""
    log = load_signal_log(path)
    if not new_rows.empty:
        run_dates = set(new_rows["date"].unique())
        log = log[~log["date"].isin(run_dates)]
        log = pd.concat([log, new_rows], ignore_index=True)
    log = log.sort_values(["date", "ticker", "signal"]).reset_index(drop=True)
    save_signal_log(path, log)
    logger.info("シグナル台帳を更新: %s (%d 行, 当日分 %d 行)", path, len(log), len(new_rows))
    return log


def forward_returns(
    log: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    horizons: list[int] | None = None,
) -> pd.DataFrame:
    """台帳の各行に、エントリー後 N 営業日のリターンと現時点リターンを付ける。

    Returns:
        入力に ret_{h} (h ∈ horizons, 未到達は NaN), ret_now, days_elapsed を加えたもの。
        既に値が入っている ret_{h} は再計算しない (確定値として保持)。
        株価が無い銘柄は未確定のまま NaN。
    """
    horizons = list(horizons or DEFAULT_HORIZONS)
    out = log.copy()
    for h in horizons:
        if f"ret_{h}" not in out.columns:
            out[f"ret_{h}"] = np.nan
        out[f"ret_{h}"] = pd.to_numeric(out[f"ret_{h}"], errors="coerce")
    out["ret_now"] = np.nan
    out["days_elapsed"] = np.nan
    if out.empty:
        return out

    for idx, row in out.iterrows():
        df = prices.get(row["ticker"])
        if df is None or df.empty:
            continue
        close = df["close"].astype(float)
        pos = _locate(close.index, row["bar_date"])
        if pos is None:
            continue
        entry = float(close.iloc[pos])
        if not entry > 0:
            continue
        last_pos = len(close) - 1
        out.at[idx, "days_elapsed"] = last_pos - pos
        out.at[idx, "ret_now"] = float(close.iloc[last_pos]) / entry - 1.0
        for h in horizons:
            col = f"ret_{h}"
            if pd.isna(out.at[idx, col]) and pos + h <= last_pos:
                out.at[idx, col] = float(close.iloc[pos + h]) / entry - 1.0
    return out


def summarize_returns(
    entries: pd.DataFrame,
    horizons: list[int],
    group_col: str = "signal",
    baseline: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """シグナル種別ごとの成績表を作る。

    各行: group, entries (件数), そして horizon ごとに n_{h} (評価済み件数),
    win_{h} (勝率: リターン>0 の割合), mean_{h}, median_{h}。
    baseline (全営業日のリターン) を渡すと excess_{h} = mean - baseline mean を加える。
    """
    rows = []
    groups = [(g, sub) for g, sub in entries.groupby(group_col)] if not entries.empty else []
    groups.append(("all", entries))
    for name, sub in groups:
        row: dict[str, Any] = {group_col: name, "entries": int(len(sub))}
        for h in horizons:
            col = f"ret_{h}"
            r = sub[col].dropna() if col in sub.columns else pd.Series(dtype=float)
            row[f"n_{h}"] = int(len(r))
            row[f"win_{h}"] = float((r > 0).mean()) if len(r) else np.nan
            row[f"mean_{h}"] = float(r.mean()) if len(r) else np.nan
            row[f"median_{h}"] = float(r.median()) if len(r) else np.nan
            if baseline is not None and f"ret_{h}" in baseline.columns:
                b = baseline[f"ret_{h}"].dropna()
                row[f"excess_{h}"] = row[f"mean_{h}"] - float(b.mean()) if len(r) and len(b) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def _locate(index: pd.Index, date_str: Any) -> int | None:
    """日付文字列に対応する位置を返す。完全一致が無ければ直前の営業日 (無ければ None)。"""
    try:
        ts = pd.Timestamp(date_str)
    except (TypeError, ValueError):
        return None
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    ts = ts.normalize()
    pos = idx.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    # 判定日より5営業日以上前の足しか無い場合は不一致とみなす
    if (ts - idx[pos].normalize()).days > 7:
        return None
    return int(pos)
