"""Mini deep dive: 단일 종목 catalyst 검증용 1페이지 보고서.

5pct-radar 의 filing intel 보고서 (5%+ 신고 단건) 를 *종목 수준* 으로 확장.
activist-scout 의 deep_dive 가 universe 외 종목 (예: LF) 에 동작하지 않을 때
대안으로 사용.

수집 데이터:
  - DART company / hyslrSttus / otrCprInvstmntSttus / majorstock / list
  - filing_intel_index 의 해당 종목 모든 분석 결과
  - catalyst chain (180일)

⚠️ activist-scout 의 deep_dive v5 helpers (ROE, peer, foreign trend 등) 는
없음. *경량 분석* 임을 명시.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .catalyst_chain import build_chain
from .config import CORP_MAP_FILE, FILING_INTEL_DIR
from .dart_client import dart_get


def fetch_company(corp_code: str) -> dict[str, Any]:
    j = dart_get("company.json", {"corp_code": corp_code})
    return j if j and j.get("status") == "000" else {}


def fetch_hyslrSttus(corp_code: str, year: int) -> list[dict[str, Any]]:
    j = dart_get(
        "hyslrSttus.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
    )
    return list(j.get("list", [])) if j and j.get("status") == "000" else []


def fetch_subsidiaries(corp_code: str, year: int) -> list[dict[str, Any]]:
    j = dart_get(
        "otrCprInvstmntSttus.json",
        {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"},
    )
    return list(j.get("list", [])) if j and j.get("status") == "000" else []


def fetch_majorstock_full(corp_code: str) -> list[dict[str, Any]]:
    j = dart_get("majorstock.json", {"corp_code": corp_code})
    return list(j.get("list", [])) if j and j.get("status") == "000" else []


def lookup_filings_in_index(stock_code: str) -> list[dict[str, Any]]:
    """filing_intel_index 에서 해당 종목 분석 결과 모두."""
    idx_path = FILING_INTEL_DIR / "filing_intel_index.json"
    if not idx_path.exists():
        return []
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    return [
        {"rcept_no": rn, **e}
        for rn, e in idx.items()
        if e.get("issuer_ticker") == stock_code
    ]


def render_deep_dive_lite(stock_code: str, *, bsns_year: int = 2024) -> str:
    """간이 deep dive Markdown."""
    cm = json.loads(CORP_MAP_FILE.read_text(encoding="utf-8"))
    info = cm.get(stock_code)
    if not info:
        return f"⚠️ stock_code {stock_code} 매핑 실패 — `--build-corp-map` 먼저 실행"
    corp_code = info["corp_code"]
    corp_name = info["corp_name"]

    company = fetch_company(corp_code)
    shareholders = fetch_hyslrSttus(corp_code, bsns_year)
    subs = fetch_subsidiaries(corp_code, bsns_year)
    all_majorstock = fetch_majorstock_full(corp_code)
    index_entries = lookup_filings_in_index(stock_code)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    out: list[str] = []
    out.append(f"# Deep Dive Lite — {corp_name} ({stock_code})")
    out.append("")
    out.append(f"*Generated: {now} · 5pct-radar v0.1.0*")
    out.append("")
    out.append(f"> ⚠️ **경량 분석.** activist-scout 의 v5 deep_dive (ROE/peer/외국인/")
    out.append(f">    임원보수 등) 대비 *간소화* 됨. 사람 검증 필수.")
    out.append("")

    # §1 회사 개요
    out.append("## §1. 회사 개요")
    out.append("")
    out.append("| 항목 | 값 |")
    out.append("|---|---|")
    out.append(f"| 회사명 | {company.get('corp_name','?')} |")
    out.append(f"| 대표 | {company.get('ceo_nm','?')} |")
    out.append(f"| 본사 | {company.get('adres','?')} |")
    out.append(f"| 설립 | {company.get('est_dt','?')} |")
    out.append(f"| 업종코드 | {company.get('induty_code','?')} |")
    out.append(f"| 회계연도 | {company.get('acc_mt','?')}월 결산 |")
    out.append(f"| 종목코드 | {stock_code} |")
    out.append(f"| DART corp_code | {corp_code} |")
    out.append("")

    # §2 최대주주
    out.append(f"## §2. 최대주주 구조 ({bsns_year}년 사업보고서)")
    out.append("")
    if not shareholders:
        out.append("(데이터 없음)")
    else:
        out.append("| 주주 | 관계 | 주식수 | 지분율 |")
        out.append("|---|---|---:|---:|")
        for s in shareholders[:10]:
            nm = s.get("nm", "?")[:30]
            rel = (s.get("relate", "") or "")[:15]
            qta = s.get("trmend_posesn_stock_co", "?")
            rt = s.get("trmend_posesn_stock_qota_rt", "?")
            out.append(f"| {nm} | {rel} | {qta} | {rt}% |")
    out.append("")

    # §3 자회사 (Sum-of-parts NAV 기초)
    out.append(f"## §3. 자회사·타법인 출자 ({bsns_year}년 사업보고서)")
    out.append("")
    if not subs:
        out.append("(데이터 없음)")
    else:
        out.append(f"총 {len(subs)}개 자회사·투자.")
        out.append("")
        out.append("| 자회사 | 지분(%) | 목적 |")
        out.append("|---|---:|---|")
        for s in subs[:15]:
            nm = (s.get("inv_prm", "") or "")[:30]
            rt = s.get("inv_qota_rt", "?")
            purp = (s.get("invstmnt_purps", "") or "")[:20]
            out.append(f"| {nm} | {rt} | {purp} |")
        if len(subs) > 15:
            out.append(f"| ... ({len(subs)-15}개 더) | | |")
    out.append("")

    # §4 5%+ 신고 전체 history
    out.append("## §4. 5% 대량보유 신고 history (DART majorstock)")
    out.append("")
    if not all_majorstock:
        out.append("(데이터 없음)")
    else:
        # 최근 → 과거 순
        ms = sorted(all_majorstock, key=lambda x: x.get("rcept_dt", ""), reverse=True)
        out.append(f"총 {len(ms)}건. 최근 15건:")
        out.append("")
        out.append("| 신고일 | 보고자 | 지분 % | Δ | rcept_no |")
        out.append("|---|---|---:|---:|---|")
        for m in ms[:15]:
            dt = m.get("rcept_dt", "")
            repror = (m.get("repror", "") or "")[:25]
            stkrt = m.get("stkrt", "?")
            stkrt_irds = m.get("stkrt_irds", "?")
            rn = m.get("rcept_no", "")
            out.append(f"| {dt} | {repror} | {stkrt} | {stkrt_irds} | {rn} |")
    out.append("")

    # §5 5pct-radar 분석 결과
    out.append("## §5. 5pct-radar 자동 분석 결과 (이 종목)")
    out.append("")
    if not index_entries:
        out.append("*(아직 5pct-radar 가 분석한 신고 없음)*")
    else:
        out.append("| 신고일 | 보고자 | 시나리오 | EV mean | confidence |")
        out.append("|---|---|---|---:|---|")
        for e in sorted(index_entries, key=lambda x: x.get("file_date", ""), reverse=True):
            dt = e.get("file_date", "")
            filer = (e.get("filer_name", "") or "")[:25]
            scn = e.get("scenario", "")
            ev = e.get("ev_mean_pct", 0) or 0
            conf = e.get("confidence", "")
            out.append(f"| {dt} | {filer} | {scn} | {ev:+.1f}% | {conf} |")
        out.append("")
        out.append("**상세 분석 보고서:**")
        for e in index_entries:
            out.append(f"- `{e['rcept_no']}` — `{e.get('report_path','?')}`")
    out.append("")

    # §6 catalyst chain (최근 5%+ 신고 기준 180일)
    if all_majorstock:
        latest = max(all_majorstock, key=lambda x: x.get("rcept_dt", ""))
        latest_dt = latest.get("rcept_dt", "").replace("-", "")
        if latest_dt:
            chain = build_chain(corp_code, latest_dt, window_days=180)
            out.append("## §6. Catalyst chain (가장 최근 5%+ 신고 후 180일)")
            out.append("")
            out.append(f"기준 신고: {latest_dt} / {latest.get('repror','?')}")
            out.append("")
            out.append(f"- 총 후속 공시: {chain['total_filings']}건 "
                       f"(분류 {sum(chain['category_counts'].values())} / "
                       f"미분류 {chain['uncategorized_count']})")
            if chain["category_counts"]:
                out.append("")
                out.append("**카테고리별:**")
                for cat, cnt in sorted(chain["category_counts"].items(),
                                       key=lambda kv: -kv[1]):
                    out.append(f"- {cat}: {cnt}건")
            if chain["categorized"]:
                out.append("")
                out.append("**Timeline (최근 10건):**")
                out.append("| 일자 | 카테고리 | 보고서명 |")
                out.append("|---|---|---|")
                for c in chain["categorized"][-10:]:
                    nm = c["report_nm"][:50]
                    out.append(f"| {c['rcept_dt']} | {c['category']} | {nm} |")
            out.append("")

    # §7 종합 / 사람 검증
    out.append("## §7. 종합 + ⚠️ 사람 검증 필수 항목")
    out.append("")
    if index_entries:
        # 최근 시나리오로 종합
        latest_e = max(index_entries, key=lambda x: x.get("file_date", ""))
        scn = latest_e.get("scenario", "?")
        ev = latest_e.get("ev_mean_pct", 0) or 0
        out.append(f"가장 최근 5pct-radar 분류: **{scn}, 12M EV mean {ev:+.1f}%**.")
        out.append("")
    out.append("**이 종목 진입 결정 전 사람이 직접 확인해야 할 항목:**")
    out.append("")
    out.append("- 보고자/펀드의 *과거 캠페인 패턴* — 우호적 vs 적극적 행동주의 구분")
    out.append("- 회사 *거버넌스 약점* (자사주 정책, 배당, 일감 등) — 캠페인 leverage 존재 여부")
    out.append("- 최대주주 *방어 능력* (지분율, 우호주주 연합)")
    out.append("- 동종업계 *밸류에이션* (PBR/PER median 대비 디스카운트)")
    out.append("- *외국계 펀드* 한국 진입 신호 (Bloomberg/헤드헌터)")
    out.append("- 매니지먼트 *인터뷰*, 산업 가십, 경쟁사 동향")
    out.append("- 분기 사업보고서 *§V 부채* + *§VIII 특수관계자* 직접 검증")
    out.append("")
    out.append("---")
    out.append("")
    out.append("*본 보고서는 자동 분석 초안이며 투자 권유가 아닙니다. "
               "최종 의사결정은 사람이 합니다. DISCLAIMER.md 참조.*")

    return "\n".join(out)


def save_deep_dive_lite(stock_code: str, *, bsns_year: int = 2024) -> Path:
    md = render_deep_dive_lite(stock_code, bsns_year=bsns_year)
    FILING_INTEL_DIR.mkdir(parents=True, exist_ok=True)
    path = FILING_INTEL_DIR / f"deep_dive_lite_{stock_code}.md"
    path.write_text(md, encoding="utf-8")
    return path
