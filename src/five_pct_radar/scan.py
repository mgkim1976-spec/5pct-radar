"""배치 모드: DART list.json 으로 최근 N일 *모든* 5% 대량보유 신고 일괄 수집·분석.

  fetch_recent_filings(days, market_filter)  → 신고 목록 [{rcept_no, corp_name, ...}]
  scan_recent(days, max_filings, ...)        → 각 신고에 run_one 적용 (인덱스로 중복 skip)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import FILING_INTEL_DIR
from .dart_client import dart_get


def fetch_recent_filings(days: int = 1) -> list[dict[str, Any]]:
    """최근 N일 KOSPI + KOSDAQ + KONEX 의 *모든* 5% 대량보유 신고."""
    end = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    return _fetch_filings_range(bgn, end)


def _fetch_filings_range(bgn: str, end: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while page <= 20:
        j = dart_get(
            "list.json",
            {
                "bgn_de": bgn,
                "end_de": end,
                "pblntf_ty": "D",       # D = 대량보유 (5%+)
                "page_no": page,
                "page_count": 100,
            },
        )
        if not j or j.get("status") != "000":
            break
        out.extend(j.get("list", []))
        total_page = int(j.get("total_page", 1))
        if int(j.get("page_no", 1)) >= total_page:
            break
        page += 1
    return out


def _load_index() -> dict[str, Any]:
    p = FILING_INTEL_DIR / "filing_intel_index.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def filter_new_filings(filings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """이미 처리된 rcept_no 는 skip. (new, skipped_count)."""
    idx = _load_index()
    new = [f for f in filings if f.get("rcept_no") not in idx]
    return new, len(filings) - len(new)


def scan_recent(
    days: int = 1,
    *,
    max_filings: int | None = None,
    do_grounding: bool = False,
    market_filter: str | None = None,
) -> tuple[list[Path], dict[str, int]]:
    """최근 N일 신고 일괄 분석.

    Args:
        days: 최근 며칠 (오늘 포함, 0 = 오늘만)
        max_filings: 처리할 최대 신고 수 (비용 통제, None = 무제한)
        do_grounding: Google grounding 사용 여부 (배치에서는 보통 False — 단건 재실행 시 True)
        market_filter: None / "Y" (KOSPI) / "K" (KOSDAQ) / "N" (KONEX)

    Returns:
        (생성된 보고서 경로 리스트, 통계 dict)
    """
    from .__main__ import run_one  # 지연 import (순환 회피)

    filings = fetch_recent_filings(days)
    print(f"\nDART 신고 (최근 {days}일): {len(filings)}건 발견")

    if market_filter:
        filings = [f for f in filings if f.get("corp_cls") == market_filter]
        print(f"  → market_filter={market_filter}: {len(filings)}건")

    new, skipped = filter_new_filings(filings)
    print(f"  → 중복 skip {skipped}건 / 신규 {len(new)}건")

    if max_filings is not None and len(new) > max_filings:
        print(f"  → max_filings={max_filings} 적용 (나머지 {len(new) - max_filings}건은 다음 실행)")
        new = new[:max_filings]

    paths: list[Path] = []
    stats = {"total": len(new), "succeeded": 0, "failed": 0}

    for i, f in enumerate(new, 1):
        rcept_no = f.get("rcept_no", "")
        stock_code = f.get("stock_code", "") or None
        corp_name = f.get("corp_name", "")
        report_nm = f.get("report_nm", "")
        market = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}.get(f.get("corp_cls", ""), "?")

        print(f"\n{'#' * 70}")
        print(f"## [{i}/{len(new)}] {corp_name} ({stock_code}, {market})")
        print(f"## {report_nm}")
        print(f"## rcept_no {rcept_no}")
        print('#' * 70)

        try:
            path = run_one(rcept_no, stock_code=stock_code, do_grounding=do_grounding)
            if path:
                paths.append(path)
                stats["succeeded"] += 1
            else:
                stats["failed"] += 1
        except Exception as e:
            print(f"  ✗ exception: {e}")
            stats["failed"] += 1

    print(f"\n{'=' * 70}")
    print(f"배치 완료: 성공 {stats['succeeded']}/{stats['total']}, 실패 {stats['failed']}")
    print('=' * 70)
    return paths, stats


def summarize_recent(limit: int = 20) -> str:
    """filing_intel_index.json 의 가장 최근 N건 시나리오 요약 (운영 dashboard)."""
    idx = _load_index()
    if not idx:
        return "(분석된 신고 없음)"
    # file_date 내림차순
    rows = sorted(idx.items(), key=lambda kv: kv[1].get("file_date", ""), reverse=True)[:limit]
    out = []
    out.append("| 신고일 | 종목 | 보고자 | 시나리오 | EV mean | **FT Score** |")
    out.append("|---|---|---|---|---:|---|")
    for rcept_no, e in rows:
        nm = e.get("issuer_name", "")
        tk = e.get("issuer_ticker", "")
        filer = (e.get("filer_name", "") or "")[:24]
        scn = e.get("scenario", "")
        ev = e.get("ev_mean_pct", 0) or 0
        fs = e.get("ft_score")
        fl = e.get("ft_label", "")
        score_cell = f"**{fs}** {fl}" if fs is not None else "—"
        out.append(f"| {e.get('file_date','')} | {nm}({tk}) | {filer} | {scn} | "
                   f"{ev:+.1f}% | {score_cell} |")
    return "\n".join(out)
