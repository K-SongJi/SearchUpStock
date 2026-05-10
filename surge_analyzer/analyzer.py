from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass(frozen=True)
class ScoreItem:
    name: str
    score: int
    max_score: int
    details: tuple[str, ...]


@dataclass(frozen=True)
class AnalysisResult:
    input_symbol: str
    yahoo_symbol: str
    close: float = 0.0
    metrics: dict[str, float] = field(default_factory=dict)
    score_items: tuple[ScoreItem, ...] = ()
    pre_filter_score: int = 0
    final_score: int = 0
    final_grade: str = "데이터 부족"
    final_opinion: str = "분석 가능한 데이터가 부족합니다."
    good_conditions: tuple[str, ...] = ()
    risk_conditions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    stop_loss_candidate: str = ""
    error: str | None = None

    @property
    def is_error(self) -> bool:
        return self.error is not None


def normalize_symbol(symbol: str) -> str:
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise ValueError("종목 코드를 입력해 주세요.")

    if cleaned.endswith((".KS", ".KQ")):
        return cleaned

    if cleaned.isdigit() and len(cleaned) == 6:
        return f"{cleaned}.KS"

    return cleaned


def analyze_symbol(symbol: str, period: str = "1y") -> AnalysisResult:
    yahoo_symbol = normalize_symbol(symbol)
    prices = _download_prices(yahoo_symbol, period)
    return analyze_prices(symbol, yahoo_symbol, prices)


def analyze_many(symbols: Iterable[str], period: str = "1y") -> list[AnalysisResult]:
    results: list[AnalysisResult] = []
    for symbol in symbols:
        try:
            results.append(analyze_symbol(symbol, period=period))
        except Exception as exc:
            yahoo_symbol = normalize_symbol(symbol) if symbol.strip() else symbol
            results.append(
                AnalysisResult(
                    input_symbol=symbol,
                    yahoo_symbol=yahoo_symbol,
                    error=str(exc),
                    warnings=(str(exc),),
                )
            )

    return sorted(results, key=lambda result: result.final_score, reverse=True)


def analyze_many_df(symbols: Iterable[str], period: str = "1y") -> pd.DataFrame:
    columns = [
        "종목",
        "조회코드",
        "현재종가",
        "필터전점수",
        "최종점수",
        "최종등급",
        "최종의견",
        "주의사항",
        "오류",
    ]
    rows = []
    for result in analyze_many(symbols, period=period):
        row = {
            "종목": result.input_symbol,
            "조회코드": result.yahoo_symbol,
            "현재종가": result.close,
            "필터전점수": result.pre_filter_score,
            "최종점수": result.final_score,
            "최종등급": result.final_grade,
            "최종의견": result.final_opinion,
            "주의사항": " / ".join(result.warnings),
            "오류": result.error or "",
        }
        row.update(result.metrics)
        rows.append(row)

    frame = pd.DataFrame(rows, columns=columns if not rows else None)
    if frame.empty:
        return frame
    return frame.sort_values("최종점수", ascending=False, ignore_index=True)


def analyze_prices(input_symbol: str, yahoo_symbol: str, prices: pd.DataFrame) -> AnalysisResult:
    prepared = _prepare_indicators(prices)
    latest = prepared.iloc[-1]
    previous = prepared.iloc[-2]

    metrics = _latest_metrics(latest)
    score_items, good_conditions, risk_conditions, warnings = _score_all(prepared)
    pre_filter_score = int(sum(item.score for item in score_items))
    final_score, filter_notes = _apply_volume_filter(pre_filter_score, prepared)
    warnings.extend(filter_notes)

    final_grade = _grade(final_score)
    final_opinion = _opinion(final_grade, good_conditions, risk_conditions, metrics)
    stop_loss_candidate = _stop_loss_candidate(latest, previous)

    return AnalysisResult(
        input_symbol=input_symbol,
        yahoo_symbol=yahoo_symbol,
        close=float(latest["Close"]),
        metrics=metrics,
        score_items=tuple(score_items),
        pre_filter_score=pre_filter_score,
        final_score=final_score,
        final_grade=final_grade,
        final_opinion=final_opinion,
        good_conditions=tuple(good_conditions),
        risk_conditions=tuple(risk_conditions),
        warnings=tuple(dict.fromkeys(warnings)),
        stop_loss_candidate=stop_loss_candidate,
    )


def get_price_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    return _prepare_indicators(_download_prices(normalize_symbol(symbol), period))


def _download_prices(symbol: str, period: str) -> pd.DataFrame:
    data = yf.download(symbol, period=period, auto_adjust=True, progress=False)
    if data.empty:
        raise ValueError(f"{symbol}의 시세 데이터를 찾지 못했습니다.")

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"{symbol} 데이터에 필요한 항목이 없습니다: {', '.join(sorted(missing))}")

    data = data.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    if len(data) < 80:
        raise ValueError(f"{symbol} 분석에는 최소 80거래일 데이터가 필요합니다. 현재 {len(data)}개입니다.")

    return data


def _prepare_indicators(prices: pd.DataFrame) -> pd.DataFrame:
    df = prices.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"].replace(0, np.nan)

    df["MA5"] = close.rolling(5).mean()
    df["MA20"] = close.rolling(20).mean()
    df["MA60"] = close.rolling(60).mean()
    df["RSI14"] = _rsi(close, 14)

    df["BB_MID"] = df["MA20"]
    std20 = close.rolling(20).std()
    df["BB_UPPER"] = df["BB_MID"] + std20 * 2
    df["BB_LOWER"] = df["BB_MID"] - std20 * 2
    df["BB_WIDTH"] = (df["BB_UPPER"] - df["BB_LOWER"]) / df["BB_MID"] * 100
    df["BB_WIDTH_AVG60"] = df["BB_WIDTH"].rolling(60).mean()

    df["DISPARITY20"] = close / df["MA20"] * 100

    lowest14 = low.rolling(14).min()
    highest14 = high.rolling(14).max()
    fast_k = (close - lowest14) / (highest14 - lowest14) * 100
    df["STO_K"] = fast_k.rolling(3).mean()
    df["STO_D"] = df["STO_K"].rolling(3).mean()

    df["VOL_AVG20"] = volume.rolling(20).mean()
    df["VOL_RATIO"] = volume / df["VOL_AVG20"]
    df["PRICE_UP"] = close > close.shift(1)
    df["VOLUME_UP"] = volume > volume.shift(1)
    df["LONG_UPPER_WICK"] = _long_upper_wick(df)

    return df.dropna().copy()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _long_upper_wick(df: pd.DataFrame) -> pd.Series:
    candle_range = (df["High"] - df["Low"]).replace(0, np.nan)
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    return (upper_wick / candle_range >= 0.45) & (df["Close"] < df["Open"])


def _latest_metrics(row: pd.Series) -> dict[str, float]:
    keys = [
        "MA5",
        "MA20",
        "MA60",
        "RSI14",
        "BB_UPPER",
        "BB_MID",
        "BB_LOWER",
        "BB_WIDTH",
        "DISPARITY20",
        "STO_K",
        "STO_D",
        "Volume",
        "VOL_AVG20",
        "VOL_RATIO",
    ]
    return {key: float(row[key]) for key in keys}


def _score_all(df: pd.DataFrame) -> tuple[list[ScoreItem], list[str], list[str], list[str]]:
    score_items: list[ScoreItem] = []
    good: list[str] = []
    risk: list[str] = []
    warnings: list[str] = []

    ma_item, ma_good, ma_risk = _score_moving_averages(df)
    boll_item, boll_good, boll_risk = _score_bollinger(df)
    volume_item, volume_good, volume_risk = _score_volume(df)
    rsi_item, rsi_good, rsi_risk = _score_rsi(df)
    disparity_item, disparity_good, disparity_risk = _score_disparity(df)
    stochastic_item, stochastic_good, stochastic_risk = _score_stochastic(df)

    for item, item_good, item_risk in [
        (ma_item, ma_good, ma_risk),
        (boll_item, boll_good, boll_risk),
        (volume_item, volume_good, volume_risk),
        (rsi_item, rsi_good, rsi_risk),
        (disparity_item, disparity_good, disparity_risk),
        (stochastic_item, stochastic_good, stochastic_risk),
    ]:
        score_items.append(item)
        good.extend(item_good)
        risk.extend(item_risk)

    latest = df.iloc[-1]
    if latest["RSI14"] >= 70:
        warnings.append("RSI가 70 이상이라 과열 가능성이 있습니다.")
    if latest["DISPARITY20"] >= 110:
        warnings.append("20일 이격도가 110 이상이라 추격매수 위험이 큽니다.")
    if bool(latest["LONG_UPPER_WICK"]) and latest["VOL_RATIO"] >= 2:
        warnings.append("거래량 폭증 후 긴 윗꼬리가 발생해 물량 털기 가능성이 있습니다.")

    return score_items, good, risk, warnings


def _score_moving_averages(df: pd.DataFrame) -> tuple[ScoreItem, list[str], list[str]]:
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    score = 0
    details: list[str] = []
    good: list[str] = []
    risk: list[str] = []

    checks = [
        ("종가가 MA5 위", latest["Close"] > latest["MA5"], 4),
        ("종가가 MA20 위", latest["Close"] > latest["MA20"], 4),
        ("종가가 MA60 위", latest["Close"] > latest["MA60"], 4),
        ("MA5가 MA60 위", latest["MA5"] > latest["MA60"], 4),
    ]
    for label, passed, points in checks:
        if passed:
            score += points
            good.append(label)
            details.append(f"{label}: 충족 +{points}")
        else:
            details.append(f"{label}: 미충족")

    golden_cross = previous["MA5"] <= previous["MA60"] and latest["MA5"] > latest["MA60"]
    gap_now = abs(latest["MA5"] - latest["MA60"]) / latest["MA60"] * 100
    gap_prev = abs(previous["MA5"] - previous["MA60"]) / previous["MA60"] * 100
    narrowing = gap_now < gap_prev
    clustered = max(latest["MA5"], latest["MA20"], latest["MA60"]) / min(
        latest["MA5"], latest["MA20"], latest["MA60"]
    ) <= 1.05
    near_cross = latest["MA5"] < latest["MA60"] and gap_now <= 3 and narrowing

    if clustered or golden_cross or near_cross:
        score += 4
        reason = "이동평균선 밀집" if clustered else "MA5/MA60 골든크로스 또는 임박"
        good.append(reason)
        details.append(f"{reason}: 충족 +4")
    else:
        details.append("이동평균선 밀집 또는 MA5/MA60 골든크로스 임박: 미충족")

    if latest["MA5"] < latest["MA60"] and latest["MA5"] < previous["MA5"]:
        risk.append("MA5가 MA60 아래에서 하락 중")

    return ScoreItem("1단계 이동평균선", score, 20, tuple(details)), good, risk


def _score_bollinger(df: pd.DataFrame) -> tuple[ScoreItem, list[str], list[str]]:
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    score = 0
    details: list[str] = []
    good: list[str] = []
    risk: list[str] = []

    width_narrow = latest["BB_WIDTH"] < latest["BB_WIDTH_AVG60"]
    width_shrinking = latest["BB_WIDTH"] < df["BB_WIDTH"].tail(20).head(5).mean()
    above_mid = latest["Close"] > latest["BB_MID"]
    upper_breakout = latest["Close"] > latest["BB_UPPER"]
    toward_upper = latest["Close"] > previous["Close"] and (
        latest["BB_UPPER"] - latest["Close"]
    ) < (previous["BB_UPPER"] - previous["Close"])

    checks = [
        ("밴드 폭이 최근 60일 평균보다 좁음", width_narrow, 5),
        ("최근 20일 동안 밴드 폭 축소 흐름", width_shrinking, 5),
        ("종가가 볼린저밴드 중심선 위", above_mid, 5),
        ("종가가 상단 밴드 방향 또는 상단 돌파", toward_upper or upper_breakout, 5),
    ]
    for label, passed, points in checks:
        if passed:
            score += points
            good.append(label)
            details.append(f"{label}: 충족 +{points}")
        else:
            details.append(f"{label}: 미충족")

    if not above_mid:
        risk.append("종가가 볼린저밴드 중심선 아래")
    if latest["Close"] < latest["BB_LOWER"]:
        risk.append("볼린저밴드 하단 방향 이탈")
    if previous["Close"] > previous["BB_UPPER"] and latest["Close"] < latest["BB_UPPER"]:
        risk.append("상단 밴드 돌파 후 밴드 안으로 밀림")

    return ScoreItem("2단계 볼린저밴드", score, 20, tuple(details)), good, risk


def _score_volume(df: pd.DataFrame) -> tuple[ScoreItem, list[str], list[str]]:
    latest = df.iloc[-1]
    score = 0
    details: list[str] = []
    good: list[str] = []
    risk: list[str] = []

    ratio = latest["VOL_RATIO"]
    checks = [
        ("현재 거래량이 20일 평균보다 큼", ratio >= 1.0, 5),
        ("현재 거래량이 20일 평균의 1.5배 이상", ratio >= 1.5, 5),
        ("현재 거래량이 20일 평균의 2배 이상", ratio >= 2.0, 5),
        ("가격 상승과 거래량 증가가 동시에 발생", bool(latest["PRICE_UP"] and latest["VOLUME_UP"]), 5),
    ]
    for label, passed, points in checks:
        if passed:
            score += points
            good.append(label)
            details.append(f"{label}: 충족 +{points}")
        else:
            details.append(f"{label}: 미충족")

    if ratio < 1:
        risk.append("거래량 없이 상승 또는 거래량 신뢰도 낮음")
    if ratio >= 2 and bool(latest["LONG_UPPER_WICK"]):
        risk.append("거래량 폭증 후 긴 윗꼬리 발생")

    return ScoreItem("3단계 거래량", score, 20, tuple(details)), good, risk


def _score_rsi(df: pd.DataFrame) -> tuple[ScoreItem, list[str], list[str]]:
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    score = 0
    details: list[str] = []
    good: list[str] = []
    risk: list[str] = []
    rsi = latest["RSI14"]

    rising_neutral = 45 <= rsi <= 55 and rsi > previous["RSI14"]
    if rising_neutral:
        score += 5
        good.append("RSI가 45~55 구간에서 상승 중")
        details.append("RSI 45~55 상승 중: 충족 +5")
    else:
        details.append("RSI 45~55 상승 중: 미충족")

    if rsi >= 50:
        score += 5
        good.append("RSI 50 이상")
        details.append("RSI 50 이상: 충족 +5")
    else:
        details.append("RSI 50 이상: 미충족")

    if 60 <= rsi < 70:
        score += 5
        good.append("RSI 60 이상 70 미만")
        details.append("RSI 60 이상 70 미만: 충족 +5")
    elif rsi >= 70:
        risk.append("RSI 70 이상 과열")
        details.append("RSI 70 이상: 과열 표시, 추가 점수 없음")
    else:
        details.append("RSI 60 이상 70 미만: 미충족")

    if rsi <= 30:
        risk.append("RSI 30 이하 과매도. 단독 매수 신호로 보지 않음")
    if previous["RSI14"] < 50 <= rsi:
        good.append("RSI 50 돌파")

    return ScoreItem("4단계 RSI", score, 15, tuple(details)), good, risk


def _score_disparity(df: pd.DataFrame) -> tuple[ScoreItem, list[str], list[str]]:
    latest = df.iloc[-1]
    score = 0
    details: list[str] = []
    good: list[str] = []
    risk: list[str] = []
    disparity = latest["DISPARITY20"]

    if disparity <= 95:
        score = 3
        details.append("이격도 95 이하: 눌림 상태 +3")
        if latest["MA5"] < latest["MA60"]:
            risk.append("이격도는 낮지만 하락 추세라 주의")
    elif 100 <= disparity <= 105:
        score = 8
        good.append("이격도 100~105 구간")
        details.append("이격도 100~105: 급등 전 후보 적정 구간 +8")
    elif 105 < disparity < 108:
        score = 5
        details.append("이격도 105~108: 상승 중이나 과열 확인 필요 +5")
    elif 108 <= disparity < 110:
        score = 2
        risk.append("이격도 108 이상 단기 과열 주의")
        details.append("이격도 108~110: 단기 과열 주의 +2")
    elif disparity >= 110:
        risk.append("이격도 110 이상 추격매수 위험")
        details.append("이격도 110 이상: 추격매수 위험 +0")
    else:
        details.append("이격도 95~100: 반등 확인 필요 +0")

    return ScoreItem("5단계 이격도", score, 15, tuple(details)), good, risk


def _score_stochastic(df: pd.DataFrame) -> tuple[ScoreItem, list[str], list[str]]:
    latest = df.iloc[-1]
    previous = df.iloc[-2]
    score = 0
    details: list[str] = []
    good: list[str] = []
    risk: list[str] = []

    golden_cross = previous["STO_K"] <= previous["STO_D"] and latest["STO_K"] > latest["STO_D"]
    dead_cross = previous["STO_K"] >= previous["STO_D"] and latest["STO_K"] < latest["STO_D"]
    both_rising = latest["STO_K"] > previous["STO_K"] and latest["STO_D"] > previous["STO_D"]

    if golden_cross:
        score += 4
        good.append("스토캐스틱 골든크로스")
        details.append("K선이 D선을 아래에서 위로 돌파: 충족 +4")
    else:
        details.append("스토캐스틱 골든크로스: 미충족")

    if golden_cross and 20 <= latest["STO_K"] <= 50:
        score += 4
        good.append("스토캐스틱 20~50 구간 골든크로스")
        details.append("골든크로스 위치 20~50: 충족 +4")
    elif golden_cross and latest["STO_K"] >= 80:
        risk.append("스토캐스틱 80 이상 골든크로스. 이미 과열일 수 있음")
        details.append("80 이상 골든크로스: 추격매수 주의")
    else:
        details.append("골든크로스 위치 20~50: 미충족")

    if both_rising:
        score += 2
        good.append("스토캐스틱 K와 D가 모두 상승")
        details.append("K와 D 모두 상승: 충족 +2")
    else:
        details.append("K와 D 모두 상승: 미충족")

    if latest["STO_K"] >= 80 and latest["STO_D"] >= 80 and dead_cross:
        risk.append("스토캐스틱 80 이상 데드크로스. 과열/조정 위험")

    return ScoreItem("6단계 스토캐스틱", score, 10, tuple(details)), good, risk


def _apply_volume_filter(score: int, df: pd.DataFrame) -> tuple[int, list[str]]:
    latest = df.iloc[-1]
    ratio = latest["VOL_RATIO"]
    notes: list[str] = []
    final_score = score

    if ratio < 1:
        final_score = max(0, final_score - 15)
        notes.append("거래량이 20일 평균보다 낮아 최종 등급을 한 단계 낮춥니다.")

    if latest["Close"] > latest["BB_UPPER"] and ratio < 1:
        notes.append("상단 밴드 돌파가 나왔지만 거래량이 부족해 가짜 돌파 가능성이 있습니다.")

    if score >= 80 and ratio < 1:
        final_score = min(final_score, 79)
        notes.append("급등 후보 강함 등급에는 최소 평균 이상의 거래량이 필요합니다.")

    if final_score >= 80 and ratio < 1:
        final_score = 79
    if ratio < 1.5 and final_score >= 90:
        final_score = 89
        notes.append("강한 돌파 후보에는 20일 평균의 1.5배 이상 거래량이 필요합니다.")

    if ratio < 1 and latest["RSI14"] > df.iloc[-2]["RSI14"]:
        notes.append("거래량이 부족하고 RSI만 상승해 신뢰도 낮음으로 표시합니다.")

    if ratio >= 2 and bool(latest["LONG_UPPER_WICK"]):
        notes.append("거래량이 2배 이상 증가했지만 긴 윗꼬리가 있어 물량 털기 가능성이 있습니다.")

    return int(max(0, min(100, final_score))), notes


def _grade(score: int) -> str:
    if score >= 80:
        return "급등 후보 강함"
    if score >= 65:
        return "관심종목"
    if score >= 50:
        return "관망"
    return "매수 부적합"


def _opinion(
    grade: str,
    good_conditions: list[str],
    risk_conditions: list[str],
    metrics: dict[str, float],
) -> str:
    if grade == "급등 후보 강함" and len(good_conditions) >= 7 and len(risk_conditions) <= 2:
        return "여러 기술적 조건이 겹친 급등 후보입니다. 다만 뉴스, 시장 분위기, 손절 기준을 함께 확인하세요."
    if grade == "관심종목" and len(good_conditions) >= 5:
        return "관심종목으로 볼 수 있습니다. 거래량 유지와 돌파 지속 여부를 확인하세요."
    if grade == "관망":
        return "일부 조건은 있으나 확신은 약합니다. 추가 거래량과 추세 확인이 필요합니다."
    if metrics.get("VOL_RATIO", 0) < 1:
        return "거래량 신뢰도가 낮아 매수 판단에는 부적합합니다."
    return "조건이 충분히 겹치지 않아 현재는 매수 부적합입니다."


def _stop_loss_candidate(row: pd.Series, previous: pd.Series) -> str:
    levels = [
        ("MA20", row["MA20"]),
        ("볼린저밴드 중심선", row["BB_MID"]),
        ("전일 저가", previous["Low"]),
    ]
    label, price = max((item for item in levels if item[1] < row["Close"]), key=lambda item: item[1], default=levels[-1])
    return f"{label} 이탈 기준 후보: {price:,.2f}"
