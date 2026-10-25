"""Forecast job: writes FORECASTS + REGION_FORECASTS for PC1 (CONTRACT §7 P3).

    flashcrowd-forecast live                      # cron: */15 * * * *   (issued_at = now floored to 15 min)
    flashcrowd-forecast live --at 2026-10-24T15:15
    flashcrowd-forecast batch --start 2026-10-22 --end 2026-10-23T23:00     # history, for boss levels + backtest

Live order per run:
  1 pull the last 8 days from the store          4 features as of issued_at (+ provenance assert)
  2 cut to what existed at issued_at              5 predict p_1..p_3, sizes, top_reasons
  3 Gemini on bursts -> CAUSE_LABELS (if key)     6 post-process -> FORECASTS / REGION_FORECASTS + JSON files
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

import pandas as pd

from . import config as C
from . import postprocess as P
from .data import HOUR, load_window
from .features import PROVENANCE, build, make_grid
from .model import Bundle
from .regions import regional_views
from .store import Store, open_store


# Fail loudly if any input used was newer than the issue time (the live twin of the training leak check).
def assert_no_lookahead(frame: pd.DataFrame) -> None:
    for c in PROVENANCE:
        bad = frame[c].notna() & (frame[c] > frame.t)
        if bad.any():
            raise RuntimeError(f"look-ahead in live features: {c} newer than issued_at for {int(bad.sum())} rows")


def live(store: Store, bundle: Bundle, issued_at=None, gemini: bool | None = None, write: bool = True) -> dict:
    # 1-2: pull, then cut to exactly what had been published/recorded at issued_at.
    issued_at = pd.Timestamp(issued_at) if issued_at else pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None).floor("15min")
    raw = load_window(store, issued_at.floor("h"), issued_at.floor("h"), label_hours=0).available(issued_at)
    raw.end = issued_at
    grid = make_grid(raw, issued_at, issued_at, every="15min" if issued_at.minute else "h")

    # 3: Gemini labels for current bursts go into CAUSE_LABELS first, then become features of this run.
    gemini = bool(os.environ.get("GEMINI_API_KEY")) if gemini is None else gemini
    n_labels = 0
    if gemini:                       # labels feed the model when it uses them, and always explain the forecast
        from .gemini_labeler import GeminiLabeler, label_bursts, write_labels
        pre = build(raw, grid, with_labels=False, with_causes=False)
        labels = label_bursts(raw, pre, GeminiLabeler(budget=40), store)
        if write:
            n_labels = write_labels(store, labels, issued_at, issued_at)
        raw.causes = pd.concat([raw.causes, labels], ignore_index=True)

    # Nothing to forecast (no edits, no published hour, nothing on the Main Page): say so instead of failing.
    if grid.empty:
        return {"issued_at": str(issued_at), "candidates": 0, "forecast_rows": 0, "region_rows": 0,
                "cause_labels": n_labels, "written": {}, "files": [], "model_version": bundle.version,
                "note": "no candidate articles at this time (is the edit recorder running and are page views loaded?)"}

    # 4-5: features as of issued_at, predictions, reasons for the rows we will publish.
    frame = build(raw, grid, with_labels=False)
    assert_no_lookahead(frame)
    pred = bundle.predict(frame)
    pred = P.select_rows(pred)
    pred = bundle.predict(pred, reasons=True)

    # 6: post-process into PC1's tables + JSON files.
    arts = P.article_rows(raw, pred, issued_at)
    regs = P.region_rows(raw, pred, issued_at)
    files = P.export_json(arts, regs)
    written = P.write(store, arts, regs, issued_at, issued_at) if write else {}
    return {"issued_at": str(issued_at), "candidates": int(len(grid)), "forecast_rows": int(len(arts)),
            "region_rows": int(len(regs)), "cause_labels": n_labels, "written": written,
            "files": [str(f) for f in files], "model_version": bundle.version}


def batch(store: Store, bundle: Bundle, start, end, write: bool = True, every: str = "h") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Forecasts for every issue hour in [start, end] (used for boss levels, the backtest and the slides).
    Features are built once for the window; each row still only sees data available at its own t."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    raw = load_window(store, start, end)
    frame = build(raw, make_grid(raw, start, end, every=every), with_labels=False)
    assert_no_lookahead(frame)
    pred = P.select_rows(bundle.predict(frame))
    pred = bundle.predict(pred, reasons=True)
    rv = regional_views(raw)
    arts, regs = [], []
    for t, g in pred.groupby("t"):
        arts.append(P.article_rows(raw, g, t))
        regs.append(P.region_rows(raw.available(t), g, t, rv))
    arts = pd.concat(arts, ignore_index=True) if arts else pd.DataFrame()
    regs = pd.concat(regs, ignore_index=True) if regs else pd.DataFrame()
    if write:
        P.write(store, arts, regs, start, end)
    return arts, regs


def main(argv=None):
    ap = argparse.ArgumentParser(description="Issue FLASHCROWD forecasts")
    sub = ap.add_subparsers(dest="mode", required=True)
    lv = sub.add_parser("live")
    lv.add_argument("--at", help="issue time (UTC); default now floored to 15 min")
    lv.add_argument("--no-write", action="store_true")
    lv.add_argument("--no-gemini", action="store_true")
    bt = sub.add_parser("batch")
    bt.add_argument("--start", required=True)
    bt.add_argument("--end", required=True)
    bt.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    store, bundle = open_store(), Bundle.load()
    if a.mode == "live":
        print(live(store, bundle, a.at, gemini=False if a.no_gemini else None, write=not a.no_write))
    else:
        arts, regs = batch(store, bundle, a.start, a.end, write=not a.no_write)
        print(f"{len(arts)} FORECASTS rows and {len(regs)} REGION_FORECASTS rows for {a.start} -> {a.end}")


if __name__ == "__main__":
    main()
