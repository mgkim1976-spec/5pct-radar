"""MGPRJ sibling-agnostic publisher — ~/.mgprj/holdings_5pct/.

Sibling projects (catalyst_driven_trading, kr_export_alpha) consume:
  - latest_v1.csv: 운용사 universe 전체 보유 (stock_code × actor flat dump)
  - snapshots/YYYYMMDD.csv: 시계열 (매집 강도 백테스트용)

Pub/sub 단방향. 5pct-radar 는 sibling 알 필요 없음.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable


SCHEMA_COLUMNS = [
    "stock_code", "corp_name", "actor", "entry_date",
    "buy_avg", "cur_price", "last_pct", "held_value",
    "unrealized_pct", "cagr_pct", "holding_days", "status", "n_buys",
]


def _mgprj_dir() -> Path:
    return Path(os.environ.get("MGPRJ_DATA_DIR", str(Path.home() / ".mgprj")))


def publish_holdings(holdings: Iterable[dict]) -> tuple[Path, Path]:
    """holdings_data["all"] → latest_v1.csv + snapshots/YYYYMMDD.csv."""
    out_dir = _mgprj_dir() / "holdings_5pct"
    snap_dir = out_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    latest = out_dir / "latest_v1.csv"
    snap = snap_dir / f"{today}.csv"

    rows = list(holdings)
    for p in (latest, snap):
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SCHEMA_COLUMNS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in SCHEMA_COLUMNS})
    return latest, snap
