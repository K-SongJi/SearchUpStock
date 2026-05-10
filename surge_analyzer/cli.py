from __future__ import annotations

import argparse
import sys

from .analyzer import AnalysisResult, analyze_many


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="기술적 조건 기반 급등 후보 보조 분석기")
    parser.add_argument("symbols", nargs="+", help="미국 티커 또는 한국 종목 코드")
    parser.add_argument("--period", default="1y", help="조회 기간 예: 6mo, 1y, 2y")
    args = parser.parse_args(argv)

    results = analyze_many(args.symbols, period=args.period)
    for result in results:
        _print_result(result)

    return 1 if any(result.is_error for result in results) else 0


def _print_result(result: AnalysisResult) -> None:
    print("=" * 88)
    print(f"종목명: {result.display_name or result.input_symbol} -> 조회 코드: {result.yahoo_symbol}")

    if result.is_error:
        print(f"데이터 부족: {result.error}")
        return

    m = result.metrics
    print(f"현재 종가: {result.close:,.2f}")
    print(f"MA5 / MA20 / MA60: {m['MA5']:,.2f} / {m['MA20']:,.2f} / {m['MA60']:,.2f}")
    print(f"RSI 14: {m['RSI14']:.2f}")
    print(
        "볼린저밴드 상단/중심/하단/폭: "
        f"{m['BB_UPPER']:,.2f} / {m['BB_MID']:,.2f} / {m['BB_LOWER']:,.2f} / {m['BB_WIDTH']:.2f}%"
    )
    print(f"20일 이격도: {m['DISPARITY20']:.2f}")
    print(f"스토캐스틱 K / D: {m['STO_K']:.2f} / {m['STO_D']:.2f}")
    print(f"현재 거래량: {m['Volume']:,.0f}")
    print(f"20일 평균 거래량: {m['VOL_AVG20']:,.0f}")
    print(f"거래량 비율: {m['VOL_RATIO']:.2f}배")
    print()
    print("지표별 점수")
    for item in result.score_items:
        print(f"- {item.name}: {item.score}/{item.max_score}점")
        for detail in item.details:
            print(f"  · {detail}")
    print()
    print(f"거래량 필터 적용 전 총점: {result.pre_filter_score}/100")
    print(f"거래량 필터 적용 후 최종 점수: {result.final_score}/100")
    print(f"최종 등급: {result.final_grade}")
    print(f"최종 의견: {result.final_opinion}")
    print(f"손절 기준 후보: {result.stop_loss_candidate}")

    if result.warnings:
        print()
        print("주의사항")
        for warning in result.warnings:
            print(f"- {warning}")


if __name__ == "__main__":
    raise SystemExit(main())
