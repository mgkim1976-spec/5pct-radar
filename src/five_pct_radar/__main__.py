"""CLI 진입점:

    python -m five_pct_radar <rcept_no>                       # 단건 분석
    python -m five_pct_radar <rcept_no> --stock-code 016740   # 종목코드 명시
    python -m five_pct_radar --build-corp-map                 # corp_code 매핑 분기 갱신
    python -m five_pct_radar --self-test                      # 두올 케이스로 자동 검증

출력:
    data/filing_intel/filing_intel_<rcept_no>.md
    data/filing_intel/filing_intel_index.json   (모든 분석 카탈로그)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import classify, extract_llm, fetch_filing, grounding, report
from .config import CORP_MAP_FILE, DATA_DIR, FILING_INTEL_DIR
from .corp_code import build_corp_code_map
from .resolve_filer import resolve


def _extract_stock_code_from_text(text: str) -> str | None:
    """본문에서 '회사코드 XXXXXX' 패턴 추출 (6자리 stock_code)."""
    m = re.search(r"회사코드\s*([0-9]{6})", text)
    return m.group(1) if m else None


def _stock_code_to_corp_code(stock_code: str) -> tuple[str | None, str | None]:
    """stock_code → (corp_code, corp_name)."""
    if not CORP_MAP_FILE.exists():
        print(f"  ⚠️ {CORP_MAP_FILE} 없음 — `--build-corp-map` 먼저 실행하세요")
        return None, None
    with open(CORP_MAP_FILE, encoding="utf-8") as f:
        cm = json.load(f)
    info = cm.get(stock_code)
    if not info:
        return None, None
    return info.get("corp_code"), info.get("corp_name")


def run_one(rcept_no: str, stock_code: str | None = None, *, do_grounding: bool = True) -> Path | None:
    """단일 5% 신고 → filing intel 보고서."""
    print(f"\n{'='*70}\n>>> 5pct-radar rcept_no={rcept_no}\n{'='*70}")

    # 1. document.xml 본문
    print("[1/6] DART document.xml 다운로드 ...")
    text = fetch_filing.fetch_document_text(rcept_no)
    if not text:
        print("  ✗ 본문 추출 실패 (ZIP 아님 또는 빈 응답)")
        return None
    print(f"  ✓ 본문 {len(text):,} chars")

    # 2. 발행회사 코드
    if not stock_code:
        stock_code = _extract_stock_code_from_text(text)
        print(f"  · 본문에서 stock_code = {stock_code}")
    corp_code, corp_name = _stock_code_to_corp_code(stock_code) if stock_code else (None, None)
    print(f"  · 발행회사 = {corp_name} (corp_code {corp_code})")

    # 3. majorstock 메타
    print("[2/6] majorstock 메타 조회 ...")
    meta = fetch_filing.fetch_majorstock_meta(rcept_no, corp_code) if corp_code else None
    file_date = ""
    if meta:
        file_date = meta.get("rcept_dt", "")
        print(f"  ✓ 신고일 {file_date}, 보고자 {meta.get('repror','?')}, 지분 {meta.get('stkrt','?')}%")
    else:
        # majorstock 은 발행회사 본인 신고만 잡으므로 제3자 신고는 미발견.
        # rcept_no 앞 8자가 신고일자 (YYYYMMDD). 폴백.
        if len(rcept_no) >= 8 and rcept_no[:8].isdigit():
            file_date = f"{rcept_no[:4]}-{rcept_no[4:6]}-{rcept_no[6:8]}"
            print(f"  · majorstock 메타 미발견 → rcept_no 폴백 신고일 {file_date}")
        else:
            print("  · majorstock 메타 미발견 (선택적)")

    # 4. LLM structured extract
    print("[3/6] Gemini structured output 으로 본문 → JSON ...")
    slices = fetch_filing.slice_around(text, fetch_filing.DEFAULT_KEYWORDS, window=600)
    extracted = extract_llm.extract_from_text(slices, full_head=text[:2000])
    if not extracted:
        print("  ✗ LLM 추출 실패")
        return None
    print(f"  ✓ 보유목적 = {extracted.get('보유목적')}, "
          f"보고구분 = {extracted.get('보고구분')}, "
          f"confidence = {extracted.get('confidence')}")

    # 5. 보고자 그룹 구조
    print("[4/6] 보고자 그룹 구조 역추적 ...")
    filer_name = extracted.get("보고자_명칭", "") or (meta.get("repror", "") if meta else "")
    if not filer_name:
        print("  · 보고자 명칭 없음 — 추적 skip")
        filer_resolution: dict[str, Any] = {
            "filer_name": "",
            "match_method": "unresolved",
            "siblings": [],
        }
    else:
        filer_resolution = resolve(filer_name)
        print(f"  ✓ match_method = {filer_resolution.get('match_method')}, "
              f"parent = {filer_resolution.get('parent_corp_name')}")

    # 6. Google grounding
    grounding_text = ""
    grounding_sources: list[dict[str, str]] = []
    grounding_queries: list[str] = []
    if do_grounding and corp_name:
        print("[5/6] Gemini Google search grounding ...")
        gr = grounding.ground_filing(
            issuer_name=corp_name,
            issuer_ticker=stock_code or "",
            filer_name=filer_name,
            parent_listed=filer_resolution.get("parent_corp_name"),
            holding_purpose=extracted.get("보유목적", ""),
            file_date=file_date,
        )
        if gr:
            grounding_text = gr.text
            grounding_sources = gr.sources
            grounding_queries = gr.queries
            print(f"  ✓ {len(grounding_queries)} queries, {len(grounding_sources)} sources")
        else:
            print("  ✗ grounding 실패 — §6 비워둠")
    else:
        print("[5/6] grounding skipped")

    # 7. 시나리오 분류
    print("[6/6] 시나리오 분류 + EV 분포 ...")
    classification = classify.classify(
        extracted=extracted,
        filer_resolution=filer_resolution,
        grounding_text=grounding_text,
    )
    if not classification:
        print("  ✗ classify 실패")
        return None
    print(f"  ✓ scenario = {classification.get('scenario')}, "
          f"EV mean = {classification.get('ev_mean_pct'):+.1f}%")

    # 7b. Follow-Trade Score 계산 (Phase 1)
    score_card = None
    try:
        from .score_filing import score_single_filing, render_score_card
        ftscore = score_single_filing(
            rcept_no=rcept_no,
            stock_code=stock_code or "",
            corp_code=corp_code or "",
            flr_nm=filer_name,
            rcept_dt=file_date,
            holding_purpose=extracted.get("보유목적", ""),
        )
        print(f"  · Follow-Trade Score = {ftscore['total']}/100 ({ftscore['label']})")
        score_card = render_score_card(ftscore)
    except Exception as e:
        print(f"  · score 계산 skip: {e}")

    # 8. 보고서 + 인덱스
    md = report.render(
        rcept_no=rcept_no,
        issuer_name=corp_name or "?",
        issuer_ticker=stock_code or "?",
        file_date=file_date,
        extracted=extracted,
        filer_resolution=filer_resolution,
        grounding_text=grounding_text,
        grounding_sources=grounding_sources,
        grounding_queries=grounding_queries,
        classification=classification,
    )
    # score 첨부 (있으면 §3 시나리오 다음에)
    if score_card:
        md = md.replace("## §4. 12개월 EV 분포",
                        score_card + "\n\n## §4. 12개월 EV 분포")
    path = report.save_report(md, FILING_INTEL_DIR, rcept_no)
    idx_payload = {
        "issuer_name": corp_name,
        "issuer_ticker": stock_code,
        "file_date": file_date,
        "filer_name": filer_name,
        "scenario": classification.get("scenario"),
        "ev_mean_pct": classification.get("ev_mean_pct"),
        "confidence": classification.get("confidence"),
        "report_path": str(path.relative_to(DATA_DIR.parent)),
    }
    if score_card:
        idx_payload["ft_score"] = ftscore["total"]
        idx_payload["ft_label"] = ftscore["label"]
    report.save_index(FILING_INTEL_DIR, rcept_no, idx_payload)
    print(f"\n✅ 저장: {path}")
    return path


def self_test() -> bool:
    """두 케이스로 자동화 재현 검증 (Phase 1 와 동일 기준).

    1) 프리미어 PE (20260504000081) — 동반 인수자 시나리오
    2) 모트렉스이에프엠 (20260430001599) — 그룹 매핑 케이스
    """
    print("\n" + "█" * 70)
    print("SELF-TEST 1: 프리미어 PE (rcept_no=20260504000081)")
    print("█" * 70)
    path1 = run_one("20260504000081", stock_code="016740", do_grounding=True)
    if not path1:
        return False

    print("\n\n" + "█" * 70)
    print("SELF-TEST 2: 모트렉스이에프엠 (rcept_no=20260430001599)")
    print("█" * 70)
    path2 = run_one("20260430001599", stock_code="016740", do_grounding=False)
    if not path2:
        return False

    idx = json.loads((FILING_INTEL_DIR / "filing_intel_index.json").read_text(encoding="utf-8"))

    print("\n\n" + "=" * 70)
    print("CHECKS")
    print("=" * 70)
    all_ok = True

    e1 = idx.get("20260504000081", {})
    md1 = path1.read_text(encoding="utf-8")
    e2 = idx.get("20260430001599", {})
    md2 = path2.read_text(encoding="utf-8")

    checks = [
        ("[1] 발행회사 = 두올", e1.get("issuer_name") == "두올"),
        ("[1] 종목코드 = 016740", e1.get("issuer_ticker") == "016740"),
        ("[1] 보고자에 '프리미어' 포함", "프리미어" in (e1.get("filer_name") or "")),
        ("[1] 시나리오는 행동주의캠페인 아님", e1.get("scenario") not in [None, "행동주의캠페인"]),
        ("[1] 본문에 '경영권 영향' 키워드", "경영권 영향" in md1),
        ("[2] 발행회사 = 두올", e2.get("issuer_name") == "두올"),
        ("[2] 보고자에 '모트렉스' 포함", "모트렉스" in (e2.get("filer_name") or "")),
        ("[2] 시나리오는 행동주의캠페인 아님", e2.get("scenario") not in [None, "행동주의캠페인"]),
        ("[2] 본문에 '경영권 영향' 키워드", "경영권 영향" in md2),
        ("[2] 그룹 매핑 정보 보고서에 노출",
         "모트렉스" in md2 and "그룹 구조" in md2),
    ]
    for name, ok in checks:
        mark = "✓" if ok else "✗"
        if not ok:
            all_ok = False
        print(f"  {mark} {name}")

    print("-" * 70)
    print("\n🎉 SELF-TEST PASSED" if all_ok else "\n❌ SELF-TEST FAILED")
    return all_ok


SUBCOMMANDS = {"today", "dive", "position", "journal", "notify", "size", "rank", "daily", "holdings"}


def _dispatch_subcommand() -> bool:
    """첫 인자가 subcommand 면 처리 후 True. 아니면 False (기존 flag 모드)."""
    if len(sys.argv) < 2 or sys.argv[1] not in SUBCOMMANDS:
        return False
    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "today":
        from .today import save_today
        path = save_today()
        print(f"\n✅ 저장: {path}\n")
        print(path.read_text(encoding="utf-8"))
        return True

    if cmd == "dive":
        if not rest:
            print("usage: radar dive <stock_code>")
            sys.exit(2)
        from .dive import save_dive
        path = save_dive(rest[0])
        print(f"\n✅ 저장: {path}")
        return True

    if cmd == "position":
        from . import position as pos_mod
        sub = rest[0] if rest else "list"
        args = rest[1:]
        if sub == "add":
            ap = argparse.ArgumentParser(prog="radar position add")
            ap.add_argument("stock_code")
            ap.add_argument("--price", type=float, required=True)
            ap.add_argument("--shares", type=int, required=True)
            ap.add_argument("--actor", default="", help="follow 한 운용사")
            ap.add_argument("--note", default="", help="§13 메모")
            ap.add_argument("--capital", type=float, default=0,
                            help="총 자본 (원) — 사이즈 검증용. 0이면 skip")
            a = ap.parse_args(args)
            p = pos_mod.add_position(a.stock_code, a.price, a.shares,
                                     actor_followed=a.actor, note=a.note)
            print(f"✅ 포지션 추가: {p['corp_name']} ({p['stock_code']}) "
                  f"@ {p['entry_price']:,.0f}원 × {p['shares']:,}주")
            print(f"   익절 {p['take_profit']:,}원 / 손절 {p['stop_loss']:,}원")
            # 사이즈 검증 (자본 지정 시)
            if a.actor and a.capital > 0:
                from .sizing import recommend_size
                rec = recommend_size(a.actor, a.capital, a.price)
                if rec.get("matched") and rec.get("recommended_pct"):
                    actual_pct = (a.shares * a.price) / a.capital * 100
                    recommended = rec["recommended_pct"]
                    if actual_pct > recommended * 1.5:
                        print(f"\n⚠️  현재 진입 {actual_pct:.2f}% 가 권장 {recommended:.2f}% 의 1.5배 초과")
                        print(f"    Kelly 추천: {rec['shares']:,}주 ({rec['recommended_won']:,.0f}원)")
                    else:
                        print(f"\n📐 사이즈 OK ({actual_pct:.2f}% / 권장 {recommended:.2f}%)")
        elif sub == "list":
            print(pos_mod.render_position_list())
        elif sub == "close":
            ap = argparse.ArgumentParser(prog="radar position close")
            ap.add_argument("stock_code")
            ap.add_argument("--price", type=float, required=True)
            ap.add_argument("--note", default="")
            a = ap.parse_args(args)
            c = pos_mod.close_position(a.stock_code, a.price, note=a.note)
            print(f"✅ 청산: {c['corp_name']} ({c['stock_code']}) {c['return_pct']:+.1f}% "
                  f"({c['holding_days']}일 보유)")
        elif sub == "note":
            if len(args) < 2:
                print("usage: radar position note <stock_code> \"<text>\"")
                sys.exit(2)
            p = pos_mod.annotate_position(args[0], " ".join(args[1:]))
            print(f"✅ 메모 추가: {p['corp_name']} ({p['stock_code']})")
        else:
            print("usage: radar position {add|list|close|note} ...")
            sys.exit(2)
        return True

    if cmd == "journal":
        from .journal import save_review
        sub = rest[0] if rest else "review"
        if sub == "review":
            path = save_review()
            print(f"\n✅ 저장: {path}\n")
            print(path.read_text(encoding="utf-8"))
        else:
            print("usage: radar journal review")
            sys.exit(2)
        return True

    if cmd == "notify":
        from .notify import send_today_summary
        ok = send_today_summary()
        print("✅ 알림 전송 성공" if ok else "⚠️ 텔레그램 미설정 또는 실패 — 위 출력 참조")
        return True

    if cmd == "size":
        from .sizing import recommend_size, render_sizing
        ap = argparse.ArgumentParser(prog="radar size")
        ap.add_argument("--actor", required=True, help="follow 할 운용사")
        ap.add_argument("--capital", type=float, required=True, help="총 자본 (원)")
        ap.add_argument("--price", type=float, required=True, help="진입가 (원)")
        ap.add_argument("--loss-pct", type=float, default=10.0, help="A1 손절 % (기본 10)")
        a = ap.parse_args(rest)
        rec = recommend_size(a.actor, a.capital, a.price,
                             override_per_share_risk_pct=a.loss_pct)
        print(render_sizing(rec))
        return True

    if cmd == "rank":
        from .rank import save_rank
        ap = argparse.ArgumentParser(prog="radar rank")
        ap.add_argument("--days", type=int, default=1, help="최근 N일 (기본 1)")
        ap.add_argument("--min-score", type=int, default=30, help="shortlist 최저 점수 (기본 30)")
        ap.add_argument("--max-dives", type=int, default=10, help="최대 dive 건수 (기본 10)")
        ap.add_argument("--include", default="",
                        help="강제 포함할 stock_code (콤마 분리, 예: 039830,093050)")
        a = ap.parse_args(rest)
        inc = [c.strip() for c in a.include.split(",") if c.strip()]
        path = save_rank(days=a.days, min_score=a.min_score, max_dives=a.max_dives, include=inc)
        print(f"\n✅ 저장: {path}\n")
        print(path.read_text(encoding="utf-8"))
        return True

    if cmd == "daily":
        from .daily import save_daily
        ap = argparse.ArgumentParser(prog="radar daily")
        ap.add_argument("--days", type=int, default=1)
        ap.add_argument("--min-score", type=int, default=30)
        ap.add_argument("--max-dives", type=int, default=10)
        a = ap.parse_args(rest)
        path = save_daily(days=a.days, min_score=a.min_score, max_dives=a.max_dives)
        print(f"\n✅ 저장: {path}\n")
        print(path.read_text(encoding="utf-8"))
        return True

    if cmd == "holdings":
        from .holdings import save_holdings
        path = save_holdings()
        print(f"\n✅ 저장: {path}\n")
        print(path.read_text(encoding="utf-8"))
        return True

    return False


def main():
    if _dispatch_subcommand():
        sys.exit(0)

    p = argparse.ArgumentParser(
        prog="five_pct_radar",
        description="5pct-radar — 한국 거래소 5% 대량보유 신고 본문 자동 분석",
        epilog="Subcommands: today, dive <code>, position {add|list|close|note}, journal review, notify",
    )
    p.add_argument("rcept_no", nargs="?", help="DART 접수번호 (14자리)")
    p.add_argument("--stock-code", help="발행회사 종목코드 (생략 시 본문에서 자동 추출)")
    p.add_argument("--no-grounding", action="store_true", help="Google grounding skip")
    p.add_argument("--self-test", action="store_true", help="두올 케이스로 자동 검증")
    p.add_argument("--build-corp-map", action="store_true",
                   help="DART corpCode.xml 다운로드 → corp_code 매핑 빌드 (분기 1회)")
    # 배치 모드 (P1)
    p.add_argument("--scan-recent", type=int, metavar="DAYS",
                   help="최근 N일 *모든* 5%%+ 신고 일괄 분석 (KOSPI+KOSDAQ+KONEX)")
    p.add_argument("--max-filings", type=int, default=None,
                   help="배치 시 처리 최대 건수 (비용 통제용)")
    p.add_argument("--market", choices=["Y", "K", "N"], default=None,
                   help="배치 시 시장 필터 (Y=KOSPI, K=KOSDAQ, N=KONEX)")
    p.add_argument("--summary", action="store_true",
                   help="filing_intel_index 의 최근 20건 시나리오 요약 출력 후 종료")
    # P2: catalyst chain
    p.add_argument("--chain", metavar="RCEPT_NO",
                   help="기존 분석된 신고의 후속 공시 timeline (180일 기본)")
    p.add_argument("--chain-window", type=int, default=180,
                   help="catalyst chain 추적 기간 (일, default 180)")
    p.add_argument("--actor-ranking", type=int, metavar="DAYS",
                   help="지난 N일 5%%+ 신고 운용사·보고자별 ranking (LLM 호출 없음)")
    p.add_argument("--actor-top-n", type=int, default=20,
                   help="ranking 표시 상위 N (default 20)")
    p.add_argument("--backtest-actor", type=int, metavar="DAYS",
                   help="지난 N일 운용사별 5%%+ 신고 follow-alpha backtest (pykrx 필요)")
    p.add_argument("--backtest-horizon", default="d365",
                   choices=["d30", "d90", "d180", "d365"],
                   help="backtest 시점 (default d365)")
    p.add_argument("--lifecycle", type=int, metavar="DAYS",
                   help="지난 N일 운용사 × 종목 *full cycle* (매집→철수) 실현/미실현 alpha (yfinance)")
    p.add_argument("--lifecycle-max-filings", type=int, default=None,
                   help="lifecycle 의 document.xml 처리 신고 수 제한 (sample 검증용)")
    p.add_argument("--phase0", metavar="LIFECYCLE_JSON",
                   help="Phase 0 score 모델 검증 — lifecycle JSON 입력 → yfinance forward return + bucket 분석")
    args = p.parse_args()

    if args.build_corp_map:
        build_corp_code_map()
        sys.exit(0)

    if args.self_test:
        ok = self_test()
        sys.exit(0 if ok else 1)

    if args.summary:
        from .scan import summarize_recent
        print(summarize_recent())
        sys.exit(0)

    if args.chain:
        from .catalyst_chain import build_chain_for_rcept_no, render_chain_markdown
        chain = build_chain_for_rcept_no(args.chain, window_days=args.chain_window)
        if chain is None:
            print(f"⚠️ rcept_no={args.chain} 는 인덱스에 없거나 corp_code 매핑 실패")
            sys.exit(1)
        print(render_chain_markdown(chain))
        sys.exit(0)

    if args.actor_ranking is not None:
        from .actor_stats import save_actor_ranking
        save_actor_ranking(days=args.actor_ranking, top_n=args.actor_top_n)
        sys.exit(0)

    if args.backtest_actor is not None:
        from .backtest_actor import run_actor_backtest
        run_actor_backtest(days=args.backtest_actor, horizon=args.backtest_horizon)
        sys.exit(0)

    if args.lifecycle is not None:
        from .lifecycle_monitor import run_lifecycle_backtest
        run_lifecycle_backtest(days=args.lifecycle, max_filings=args.lifecycle_max_filings)
        sys.exit(0)

    if args.phase0:
        from .backtest_phase0 import run_phase0
        run_phase0(Path(args.phase0))
        sys.exit(0)

    if args.scan_recent is not None:
        from .scan import scan_recent
        paths, stats = scan_recent(
            days=args.scan_recent,
            max_filings=args.max_filings,
            do_grounding=not args.no_grounding,
            market_filter=args.market,
        )
        sys.exit(0 if stats["succeeded"] >= 0 else 1)

    if not args.rcept_no:
        p.print_help()
        sys.exit(2)

    path = run_one(args.rcept_no, stock_code=args.stock_code,
                   do_grounding=not args.no_grounding)
    sys.exit(0 if path else 1)


if __name__ == "__main__":
    main()
