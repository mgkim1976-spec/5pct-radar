"""지난 N일 5% 대량보유 신고 → 보고자(운용사·개인) 별 ranking + 분류.

LLM 호출 없이 *DART 메타데이터만* 으로 *who is most active* 분석.

활용:
  - 한국 행동주의·우호적 행동주의 운용사 풀 자동 확인
  - 특정 운용사가 어떤 종목들에 진입하고 있는지 cross-stock 패턴 파악
  - 신규 운용사 출현 자동 감지 (catalyst trade 후보 universe 확장)
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from ..config import FILING_INTEL_DIR
from ..core.dart_client import dart_get


# 보고자 분류 키워드 (activist-scout domain.py 패턴 간소화)
HARDCORE_ACTIVISTS = {
    "얼라인", "Align", "차파트너스", "Cha Partners", "VIP자산운용", "브이아이피자산운용",
    "한국밸류", "Korea Value", "KCGI", "한국기업거버넌스", "안다자산운용", "Anda",
    "트러스톤", "Trust&", "Trust ", "라이프자산운용",
}
SEMI_ACTIVISTS = {
    "신영자산운용", "한국투자밸류", "베어링자산운용", "Baring", "다올자산운용",
    "DAOL", "에이티넘", "Atinum", "한투밸류",
}
PASSIVE = {
    "국민연금", "NPS", "한국투자증권", "삼성자산운용", "미래에셋자산운용",
    "KB자산운용", "신한자산운용", "BlackRock", "블랙록", "Vanguard", "iShares",
}
PE_KEYWORDS = ["사모투자", "사모펀드", "PEF", "Private Equity", "PE Fund", "엠앤에이"]


def classify_actor(name: str) -> str:
    """보고자 분류: activist / semi_activist / pe_fund / passive / individual / corporate."""
    if not name:
        return "unknown"
    for kw in HARDCORE_ACTIVISTS:
        if kw in name:
            return "activist"
    for kw in SEMI_ACTIVISTS:
        if kw in name:
            return "semi_activist"
    for kw in PE_KEYWORDS:
        if kw in name:
            return "pe_fund"
    for kw in PASSIVE:
        if kw in name:
            return "passive"
    if any(kw in name for kw in ["자산운용", "투자자문", "투자운용", "Asset", "Capital"]):
        return "corporate"
    # 법인 표기 없으면 개인일 가능성
    if any(kw in name for kw in ["주식회사", "(주)", "㈜", "Co.,", "Inc.", "Corp.", "Ltd."]):
        return "corporate"
    return "individual"


_NORMALIZE_RE = re.compile(r"(주식회사|㈜|\(주\)|Co\.,?\s*Ltd\.?|Inc\.?|Corp\.?|Limited|LLC)", re.IGNORECASE)


def normalize_actor_name(name: str) -> str:
    """보고자명 정규화 — '(주)브이아이피자산운용', 'VIP자산운용' 등 통합."""
    if not name:
        return ""
    n = _NORMALIZE_RE.sub("", name)
    n = re.sub(r"\s+", "", n).strip()
    # 한글 「브이아이피」 ↔ 영문 「VIP」 매핑
    # 단순 처리 — 진짜 매핑 dict 는 별도 필요
    return n


def fetch_filings_window(days: int = 365) -> list[dict[str, Any]]:
    """지난 N일 모든 5% 대량보유 신고 (KOSPI+KOSDAQ+KONEX).

    DART list.json 은 *corp_code 없이* 검색 시 최대 3개월 기간 제한이 있다.
    그래서 90일 chunk 단위로 분할 fetch 후 합친다.
    """
    out: list[dict[str, Any]] = []
    seen_rcept_no: set[str] = set()
    chunk_days = 90
    today = datetime.now()
    remaining = days
    chunk_end = today
    while remaining > 0:
        d = min(chunk_days, remaining)
        chunk_bgn = chunk_end - timedelta(days=d - 1)
        bgn = chunk_bgn.strftime("%Y%m%d")
        end = chunk_end.strftime("%Y%m%d")
        print(f"  · chunk {bgn} ~ {end} ({d}일)")
        page = 1
        chunk_total = 0
        while page <= 100:  # 100 페이지 × 100건 = 한 chunk 최대 10,000건
            j = dart_get(
                "list.json",
                {
                    "bgn_de": bgn,
                    "end_de": end,
                    "pblntf_ty": "D",
                    "page_no": page,
                    "page_count": 100,
                },
            )
            if not j or j.get("status") != "000":
                if j and j.get("message"):
                    print(f"    ! DART {j.get('status')}: {j.get('message')}")
                break
            items = j.get("list", [])
            for it in items:
                rn = it.get("rcept_no")
                if rn and rn not in seen_rcept_no:
                    seen_rcept_no.add(rn)
                    out.append(it)
                    chunk_total += 1
            if not items:
                break
            if int(j.get("page_no", 1)) >= int(j.get("total_page", 1)):
                break
            page += 1
        print(f"    fetched {chunk_total} 건 (누적 {len(out)})")
        # 다음 chunk 의 end 는 현 chunk 의 bgn 하루 전
        chunk_end = chunk_bgn - timedelta(days=1)
        remaining -= d
    print(f"  · 총 fetch 완료: {len(out)} 신고")
    return out


def aggregate_by_actor(filings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """보고자 (정규화명) 별 그룹화.

    Returns:
      {
        normalized_name: {
          "display_names": [원형 이름 1~N],
          "category": str (activist/...),
          "filing_count": int,
          "unique_stocks": int,
          "stocks": [stock_code, ...],
          "stock_names": [회사명, ...],
          "first_seen": str,
          "last_seen": str,
          "filings_per_stock": {stock_code: count},
        }
      }
    """
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "display_names": set(),
        "category": None,
        "filing_count": 0,
        "stocks": set(),
        "stock_names": {},
        "first_seen": "9999-99-99",
        "last_seen": "0000-00-00",
        "filings_per_stock": defaultdict(int),
    })

    for f in filings:
        flr_nm = f.get("flr_nm", "")
        if not flr_nm:
            continue
        norm = normalize_actor_name(flr_nm)
        if not norm:
            continue
        rec = out[norm]
        rec["display_names"].add(flr_nm)
        if rec["category"] is None:
            rec["category"] = classify_actor(flr_nm)
        rec["filing_count"] += 1
        sc = f.get("stock_code", "")
        if sc:
            rec["stocks"].add(sc)
            rec["stock_names"][sc] = f.get("corp_name", "")
            rec["filings_per_stock"][sc] += 1
        dt = f.get("rcept_dt", "")
        if dt and dt < rec["first_seen"]:
            rec["first_seen"] = dt
        if dt and dt > rec["last_seen"]:
            rec["last_seen"] = dt

    # tuple/set → list 변환 + unique_stocks count
    finalized = {}
    for norm, rec in out.items():
        finalized[norm] = {
            "display_names": sorted(rec["display_names"]),
            "category": rec["category"],
            "filing_count": rec["filing_count"],
            "unique_stocks": len(rec["stocks"]),
            "stocks": sorted(rec["stocks"]),
            "stock_names": dict(rec["stock_names"]),
            "first_seen": rec["first_seen"],
            "last_seen": rec["last_seen"],
            "filings_per_stock": dict(rec["filings_per_stock"]),
        }
    return finalized


def render_ranking_markdown(
    stats: dict[str, dict[str, Any]],
    *,
    top_n: int = 20,
    days: int = 365,
) -> str:
    out: list[str] = []
    end = datetime.now().strftime("%Y-%m-%d")
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out.append(f"# 5pct-radar — 운용사·보고자 ranking ({bgn} ~ {end})")
    out.append("")
    out.append(f"*총 {sum(s['filing_count'] for s in stats.values())} 건 신고, "
               f"{len(stats)} 명/법인 보고자.*")
    out.append("")

    # category 별 카운트
    cat_counts: dict[str, int] = defaultdict(int)
    cat_filings: dict[str, int] = defaultdict(int)
    for s in stats.values():
        cat_counts[s["category"]] += 1
        cat_filings[s["category"]] += s["filing_count"]

    out.append("## §0. 카테고리별 분포")
    out.append("")
    out.append("| 카테고리 | 보고자 수 | 신고 수 |")
    out.append("|---|---:|---:|")
    for cat in ["activist", "semi_activist", "pe_fund", "passive", "corporate", "individual", "unknown"]:
        if cat in cat_counts:
            out.append(f"| {cat} | {cat_counts[cat]} | {cat_filings[cat]} |")
    out.append("")

    # 🚨 행동주의 / 우호적 행동주의 ranking
    activist_recs = [(norm, s) for norm, s in stats.items()
                     if s["category"] in ("activist", "semi_activist")]
    activist_recs.sort(key=lambda kv: (-kv[1]["unique_stocks"], -kv[1]["filing_count"]))
    out.append(f"## §1. 🚨 행동주의·우호적 행동주의 운용사 (총 {len(activist_recs)} 명)")
    out.append("")
    if activist_recs:
        out.append("| Rank | 보고자 | 카테고리 | 종목 수 | 신고 수 | 첫 등장 | 최근 |")
        out.append("|---:|---|---|---:|---:|---|---|")
        for i, (norm, s) in enumerate(activist_recs[:top_n], 1):
            disp = s["display_names"][0]
            out.append(f"| {i} | {disp} | {s['category']} | {s['unique_stocks']} | "
                       f"{s['filing_count']} | {s['first_seen']} | {s['last_seen']} |")
        out.append("")
        # 각 행동주의 운용사의 종목 list
        out.append("### 행동주의 운용사별 진입 종목 list")
        out.append("")
        for norm, s in activist_recs[:10]:
            disp = s["display_names"][0]
            out.append(f"**{disp}** ({s['category']}, {s['unique_stocks']} 종목, {s['filing_count']} 신고):")
            for sc, nm in sorted(s["stock_names"].items(), key=lambda kv: -s["filings_per_stock"][kv[0]]):
                cnt = s["filings_per_stock"][sc]
                out.append(f"  - {nm} ({sc}) — {cnt}건")
            out.append("")
    else:
        out.append("*(행동주의 카테고리 운용사 미발견)*")
        out.append("")

    # PE 펀드 ranking
    pe_recs = [(norm, s) for norm, s in stats.items() if s["category"] == "pe_fund"]
    pe_recs.sort(key=lambda kv: (-kv[1]["unique_stocks"], -kv[1]["filing_count"]))
    out.append(f"## §2. PE 펀드·사모투자 (총 {len(pe_recs)} 명)")
    out.append("")
    if pe_recs:
        out.append("| Rank | 보고자 | 종목 수 | 신고 수 | 최근 |")
        out.append("|---:|---|---:|---:|---|")
        for i, (norm, s) in enumerate(pe_recs[:top_n], 1):
            disp = s["display_names"][0][:40]
            out.append(f"| {i} | {disp} | {s['unique_stocks']} | "
                       f"{s['filing_count']} | {s['last_seen']} |")
        out.append("")

    # 전체 ranking (신고 수 기준)
    all_recs = sorted(stats.items(), key=lambda kv: -kv[1]["filing_count"])
    out.append(f"## §3. 전체 top-{top_n} (신고 수 기준)")
    out.append("")
    out.append("| Rank | 보고자 | 카테고리 | 종목 수 | 신고 수 |")
    out.append("|---:|---|---|---:|---:|")
    for i, (norm, s) in enumerate(all_recs[:top_n], 1):
        disp = s["display_names"][0][:40]
        out.append(f"| {i} | {disp} | {s['category']} | {s['unique_stocks']} | {s['filing_count']} |")
    out.append("")

    out.append("---")
    out.append("")
    out.append("*분류는 단순 키워드 매칭 기반. 정확한 행동주의 패턴은 사람 검증 필수.*")

    return "\n".join(out)


def save_actor_ranking(days: int = 365, top_n: int = 20) -> tuple[Any, Any]:
    """전체 흐름: fetch → aggregate → render → save."""
    print(f"지난 {days} 일 5%+ 신고 일괄 fetch 중...")
    filings = fetch_filings_window(days)
    if not filings:
        print("⚠️ 신고 0건. DART API 응답 확인 필요.")
        return None, None
    print(f"\n보고자별 그룹화 중...")
    stats = aggregate_by_actor(filings)
    print(f"  · 총 보고자 (정규화): {len(stats)} 명/법인")

    md = render_ranking_markdown(stats, top_n=top_n, days=days)
    FILING_INTEL_DIR.mkdir(parents=True, exist_ok=True)
    end = datetime.now().strftime("%Y%m%d")
    md_path = FILING_INTEL_DIR / f"actor_ranking_{end}_{days}d.md"
    json_path = FILING_INTEL_DIR / f"actor_ranking_{end}_{days}d.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 저장: {md_path}")
    print(f"✅ 저장: {json_path}")
    return md_path, stats
