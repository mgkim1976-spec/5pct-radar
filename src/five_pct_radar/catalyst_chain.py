"""Catalyst chain: 5% 신고 *후* 동일 종목의 후속 공시 자동 추적.

한 catalyst 는 단발성이 아니라 연쇄적으로 발생한다:
  5% 신고 (D-0) → 자사주 결정 (D+30) → 주총 안건 (D+90) → 주총 (D+180)

이 모듈은 *5% 신고 이후* 같은 종목에 발생한 다음 카테고리 공시를 자동 매핑:

  - 자사주 (취득/소각/처분)        - 주총 안건 (주주제안)
  - 임원 변동 (사임/선임)          - 공시 정정 (자료 신뢰성)
  - 합병/분할/주식교환            - 우발채무 (보증 등)
  - 매매계약 결제/이행            - 거래정지/해제

⚠️ 단순 패턴 매칭 (보고서명 키워드). LLM 으로 *의미 해석* 까지는 별도 단계.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .config import FILING_INTEL_DIR
from .dart_client import dart_get


# 후속 공시 카테고리 (보고서명 키워드 매칭)
CATALYST_CATEGORIES = {
    "추가_5pct_신고": ["주식등의대량보유"],
    "임원_지분변동": ["임원ㆍ주요주주", "특정증권등소유"],
    "자사주": ["자기주식", "자사주"],
    "주총_안건": ["주주총회소집", "주주제안"],
    "임원_변동": ["임원변동", "사외이사", "대표이사", "사임", "선임"],
    "합병_분할": ["합병", "분할", "주식교환", "포괄적교환"],
    "우발채무": ["채무보증", "타인에대한", "특수관계인거래"],
    "매매계약_이행": ["매매계약", "거래종결", "양수도"],
    "정정공시": ["[정정]", "[기재정정]", "정정신고"],
    "거래정지": ["거래정지", "관리종목", "투자주의"],
    "감사_변동": ["감사인", "회계감사"],
    "배당_결정": ["현금배당", "현물배당", "배당결정"],
    "사업보고": ["사업보고서", "분기보고서", "반기보고서"],
}


def categorize_filing(report_nm: str) -> str | None:
    """보고서명에서 카테고리 추출. 매칭 없으면 None."""
    if not report_nm:
        return None
    for cat, keywords in CATALYST_CATEGORIES.items():
        for kw in keywords:
            if kw in report_nm:
                return cat
    return None


def fetch_followup_filings(
    corp_code: str,
    *,
    since_date: str,
    until_date: str | None = None,
    max_per_page: int = 100,
) -> list[dict[str, Any]]:
    """특정 corp_code 의 since_date ~ until_date 모든 공시 목록.

    since_date: "YYYYMMDD"
    until_date: "YYYYMMDD" (생략 시 오늘)
    """
    end = until_date or datetime.now().strftime("%Y%m%d")
    out: list[dict[str, Any]] = []
    page = 1
    while page <= 20:
        j = dart_get(
            "list.json",
            {
                "corp_code": corp_code,
                "bgn_de": since_date,
                "end_de": end,
                "page_no": page,
                "page_count": max_per_page,
            },
        )
        if not j or j.get("status") != "000":
            break
        out.extend(j.get("list", []))
        if int(j.get("page_no", 1)) >= int(j.get("total_page", 1)):
            break
        page += 1
    return out


def build_chain(
    corp_code: str,
    filing_date: str,
    *,
    window_days: int = 180,
) -> dict[str, Any]:
    """5% 신고 발생일로부터 window_days 동안 후속 공시 카테고리 분류·timeline.

    Args:
        corp_code: 발행회사 corp_code
        filing_date: 원 5% 신고일 (YYYYMMDD 또는 YYYY-MM-DD)
        window_days: 추적 기간

    Returns:
        {
          "anchor_date": str,
          "window_end": str,
          "total_filings": int,
          "categorized": [{rcept_dt, rcept_no, category, report_nm}, ...],
          "category_counts": {category: count},
          "uncategorized_count": int,
        }
    """
    # 날짜 정규화 (YYYYMMDD)
    anchor = filing_date.replace("-", "")[:8]
    end_dt = (datetime.strptime(anchor, "%Y%m%d") + timedelta(days=window_days)).strftime("%Y%m%d")
    end_dt = min(end_dt, datetime.now().strftime("%Y%m%d"))  # 미래 잘라냄

    filings = fetch_followup_filings(corp_code, since_date=anchor, until_date=end_dt)

    categorized: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    uncategorized = 0
    for f in filings:
        report_nm = f.get("report_nm", "")
        rcept_dt = f.get("rcept_dt", "")
        rcept_no = f.get("rcept_no", "")
        # anchor 일 자체의 *원 신고* 는 chain 에서 제외
        if rcept_dt == anchor and "대량보유" in report_nm:
            continue
        cat = categorize_filing(report_nm)
        if cat is None:
            uncategorized += 1
            continue
        categorized.append({
            "rcept_dt": rcept_dt,
            "rcept_no": rcept_no,
            "category": cat,
            "report_nm": report_nm,
        })
        counts[cat] = counts.get(cat, 0) + 1

    # 시간 순 정렬
    categorized.sort(key=lambda x: x["rcept_dt"])

    return {
        "anchor_date": anchor,
        "window_end": end_dt,
        "total_filings": len(filings),
        "categorized": categorized,
        "category_counts": counts,
        "uncategorized_count": uncategorized,
    }


def render_chain_markdown(chain: dict[str, Any]) -> str:
    """chain 결과를 Markdown timeline 으로."""
    out: list[str] = []
    out.append(f"### Catalyst chain — {chain['anchor_date']} 이후 {chain['window_end']} 까지")
    out.append("")
    if chain["total_filings"] == 0:
        out.append("*(추적 기간 내 후속 공시 없음)*")
        return "\n".join(out)
    out.append(f"- 총 후속 공시: {chain['total_filings']}건 "
               f"(분류 {sum(chain['category_counts'].values())} / 미분류 {chain['uncategorized_count']})")
    out.append("")
    if chain["category_counts"]:
        out.append("**카테고리별 카운트:**")
        for cat, cnt in sorted(chain["category_counts"].items(), key=lambda kv: -kv[1]):
            out.append(f"- {cat}: {cnt}건")
        out.append("")

    out.append("**Timeline:**")
    out.append("| 일자 | 카테고리 | 보고서명 |")
    out.append("|---|---|---|")
    for c in chain["categorized"]:
        nm = c["report_nm"]
        if len(nm) > 50:
            nm = nm[:47] + "..."
        out.append(f"| {c['rcept_dt']} | {c['category']} | {nm} |")
    return "\n".join(out)


def build_chain_for_rcept_no(rcept_no: str, *, window_days: int = 180) -> dict[str, Any] | None:
    """filing_intel_index 에서 corp_code 와 file_date 가져와서 chain 빌드."""
    idx_path = FILING_INTEL_DIR / "filing_intel_index.json"
    if not idx_path.exists():
        return None
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    entry = idx.get(rcept_no)
    if not entry:
        return None
    stock_code = entry.get("issuer_ticker")
    file_date = entry.get("file_date") or rcept_no[:8]
    if not stock_code:
        return None

    # stock_code → corp_code
    from .config import CORP_MAP_FILE
    cm = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))
    info = cm.get(stock_code)
    if not info:
        return None
    return build_chain(info["corp_code"], file_date, window_days=window_days)
