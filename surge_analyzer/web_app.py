from __future__ import annotations

import base64
import re
import os
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

os.environ.setdefault("MPLCONFIGDIR", str(Path.cwd() / ".matplotlib-cache"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from flask import Flask, render_template, request

from .analyzer import analyze_many, get_price_history, normalize_symbol
from .resolver import display_name_for_symbol


app = Flask(__name__)
plt.rcParams["font.family"] = ["Malgun Gothic", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


CONDITION_PRESETS = {
    "기본형": {
        "summary": "평소 기준으로 후보를 10~30개 정도 뽑아 우리 앱에서 2차 선별하기 위한 조건식입니다.",
        "highlights": [
            "RSI 50 이상 70 이하",
            "이격도 98 이상 108 이하",
            "거래대금 30억 이상",
            "거래량 50만 주 조건 없음",
        ],
        "rules": [
            "종가 > MA5",
            "종가 > MA20",
            "종가 > MA60",
            "볼린저밴드 중심선 이상",
            "RSI(14) 50 이상",
            "RSI(14) 70 이하",
            "이격도(20) 98 이상",
            "이격도(20) 108 이하",
            "Stochastic slow(14,3,3) %K > %D",
            "Stochastic slow %K 80 이하",
            "Volume Osc(1,20,9) Signal선 이상",
            "거래대금 3000 이상 99999 이하",
            "종가 >= 시가",
            "당일 등락률 0% 이상 12% 이하",
        ],
    },
    "정밀형": {
        "summary": "기본형에서 후보가 너무 많이 나올 때 쓰는 더 엄격한 조건식입니다.",
        "highlights": [
            "RSI 55 이상 65 이하",
            "이격도 100 이상 105 이하",
            "거래대금 50억 이상",
            "거래량 50만 주 이상",
        ],
        "rules": [
            "종가 > MA5",
            "종가 > MA20",
            "종가 > MA60",
            "볼린저밴드 중심선 이상",
            "RSI(14) 55 이상",
            "RSI(14) 65 이하",
            "이격도(20) 100 이상",
            "이격도(20) 105 이하",
            "Stochastic slow(14,3,3) %K > %D",
            "Stochastic slow %K 80 이하",
            "Volume Osc(1,20,9) Signal선 이상",
            "거래대금 5000 이상 99999 이하",
            "종가 >= 시가",
            "당일 등락률 0% 이상 12% 이하",
            "거래량 500000 이상 999999999 이하",
        ],
    },
}


@app.template_filter("market_price")
def market_price(value: float, yahoo_symbol: str) -> str:
    if yahoo_symbol.endswith((".KS", ".KQ")):
        return f"{value:,.0f}원"
    return f"${value:,.2f}"


@app.template_filter("signed_percent")
def signed_percent(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


@app.get("/")
def index():
    active_tab = request.args.get("tab", "analyze")
    return _render_index(active_tab=active_tab if active_tab in {"analyze", "verify"} else "analyze")


@app.post("/analyze")
def analyze():
    symbols_text = request.form.get("symbols", "")
    period = request.form.get("period", "1y")
    chart_symbol = request.form.get("chart_symbol", "").strip()
    symbols = _parse_symbols(symbols_text)
    results = analyze_many(symbols, period=period) if symbols else []
    matched_count = sum(1 for result in results if result.final_score >= 65 and not result.is_error)
    chart_symbols = _parse_symbols(chart_symbol)
    charts = [_chart_for_symbol(symbol, period) for symbol in chart_symbols]
    charts_by_yahoo, standalone_charts = _split_charts_for_results(charts, results)

    return render_template(
        "index.html",
        active_tab="analyze",
        symbols=symbols_text,
        period=period,
        results=results,
        matched_count=matched_count,
        total_count=len(results),
        chart_symbol=chart_symbol,
        charts_by_yahoo=charts_by_yahoo,
        standalone_charts=standalone_charts,
        verification=None,
        verification_error="",
    )


@app.post("/verify")
def verify():
    uploaded_files = [file for file in request.files.getlist("verification_file") if file and file.filename]
    verification = None
    verification_error = ""

    if not uploaded_files:
        verification_error = "검증할 엑셀 파일을 선택해주세요."
    else:
        try:
            verification = _verify_uploaded_workbooks(uploaded_files)
        except Exception as exc:
            verification_error = f"파일을 읽지 못했습니다: {exc}"

    return _render_index(active_tab="verify", verification=verification, verification_error=verification_error)


def _render_index(
    *,
    active_tab: str = "analyze",
    verification: dict | None = None,
    verification_error: str = "",
):
    return render_template(
        "index.html",
        active_tab=active_tab,
        symbols="",
        period="1y",
        results=[],
        matched_count=0,
        total_count=0,
        chart_symbol="",
        charts_by_yahoo={},
        standalone_charts=[],
        verification=verification,
        verification_error=verification_error,
        condition_presets=CONDITION_PRESETS,
    )


def _parse_symbols(symbols_text: str) -> list[str]:
    raw = symbols_text.replace(",", "\n").replace(" ", "\n").splitlines()
    return [symbol.strip() for symbol in raw if symbol.strip()]


def _chart_as_base64(symbol: str, period: str) -> str | None:
    try:
        df = get_price_history(symbol, period=period)
    except Exception:
        return None

    visible = df.tail(160)
    fig, axes = plt.subplots(
        5,
        1,
        figsize=(13, 12),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1, 1, 1, 1]},
    )
    fig.patch.set_facecolor("#f7f8f5")

    axes[0].plot(visible.index, visible["Close"], label="종가", color="#1f2937", linewidth=1.8)
    axes[0].plot(visible.index, visible["MA5"], label="MA5", color="#dc2626", linewidth=1)
    axes[0].plot(visible.index, visible["MA20"], label="MA20", color="#2563eb", linewidth=1)
    axes[0].plot(visible.index, visible["MA60"], label="MA60", color="#059669", linewidth=1)
    axes[0].plot(visible.index, visible["BB_UPPER"], label="BB 상단", color="#7c3aed", linewidth=0.9)
    axes[0].plot(visible.index, visible["BB_LOWER"], label="BB 하단", color="#7c3aed", linewidth=0.9)
    axes[0].fill_between(
        visible.index,
        visible["BB_LOWER"].to_numpy(),
        visible["BB_UPPER"].to_numpy(),
        color="#ddd6fe",
        alpha=0.22,
    )
    axes[0].set_title(f"{display_name_for_symbol(symbol)} ({normalize_symbol(symbol)}) 기술적 차트")
    axes[0].legend(loc="upper left", ncols=4, fontsize=8)

    axes[1].bar(visible.index, visible["Volume"], color="#64748b", width=1)
    axes[1].plot(visible.index, visible["VOL_AVG20"], color="#f97316", label="20일 평균 거래량")
    axes[1].legend(loc="upper left", fontsize=8)

    axes[2].plot(visible.index, visible["RSI14"], color="#0f766e")
    axes[2].axhline(70, color="#dc2626", linestyle="--", linewidth=0.8)
    axes[2].axhline(50, color="#64748b", linestyle="--", linewidth=0.8)
    axes[2].axhline(30, color="#2563eb", linestyle="--", linewidth=0.8)
    axes[2].set_ylabel("RSI")

    axes[3].plot(visible.index, visible["DISPARITY20"], color="#9333ea")
    axes[3].axhline(100, color="#64748b", linestyle="--", linewidth=0.8)
    axes[3].axhline(105, color="#f97316", linestyle="--", linewidth=0.8)
    axes[3].axhline(110, color="#dc2626", linestyle="--", linewidth=0.8)
    axes[3].set_ylabel("이격도")

    axes[4].plot(visible.index, visible["STO_K"], label="K", color="#16a34a")
    axes[4].plot(visible.index, visible["STO_D"], label="D", color="#f59e0b")
    axes[4].axhline(80, color="#dc2626", linestyle="--", linewidth=0.8)
    axes[4].axhline(20, color="#2563eb", linestyle="--", linewidth=0.8)
    axes[4].legend(loc="upper left", fontsize=8)

    for axis in axes:
        axis.grid(True, alpha=0.25)
        axis.set_facecolor("#ffffff")

    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=140)
    plt.close(fig)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("ascii")


def _chart_for_symbol(symbol: str, period: str) -> dict[str, str | None]:
    try:
        display_name = display_name_for_symbol(symbol)
        yahoo_symbol = normalize_symbol(symbol)
    except Exception:
        display_name = symbol
        yahoo_symbol = symbol

    return {
        "input": symbol,
        "display_name": display_name,
        "yahoo_symbol": yahoo_symbol,
        "image": _chart_as_base64(symbol, period),
    }


def _split_charts_for_results(charts: list[dict[str, str | None]], results: list) -> tuple[dict[str, dict], list[dict]]:
    result_symbols = {result.yahoo_symbol for result in results if not result.is_error}
    charts_by_yahoo: dict[str, dict] = {}
    standalone_charts: list[dict] = []

    for chart in charts:
        yahoo_symbol = chart.get("yahoo_symbol")
        if yahoo_symbol in result_symbols and yahoo_symbol not in charts_by_yahoo:
            charts_by_yahoo[yahoo_symbol] = chart
        else:
            standalone_charts.append(chart)

    return charts_by_yahoo, standalone_charts


def _verify_uploaded_workbooks(uploaded_files: list) -> dict:
    verifications = [_verify_uploaded_workbook(file.read(), file.filename) for file in uploaded_files]
    return _combine_verifications(verifications)


def _verify_uploaded_workbook(content: bytes, filename: str) -> dict:
    rows_by_sheet = _read_xlsx_rows(content)
    if not rows_by_sheet:
        raise ValueError("시트 데이터를 찾지 못했습니다.")

    sheet_name, rows = rows_by_sheet[0]
    raw_rows, analyzed_rows, outcome_rows, diary_rows = _extract_verification_rows(rows)
    joined_rows = _join_analysis_and_outcomes(analyzed_rows, outcome_rows, raw_rows)
    source_date = _source_date_from_filename(filename)
    for row in joined_rows:
        row["source_file"] = filename
        row["source_date"] = source_date

    result = {
        "filename": filename,
        "file_count": 1,
        "sheet_name": sheet_name,
        "raw_count": len(raw_rows),
        "analyzed_count": len(analyzed_rows),
        "outcome_count": len(outcome_rows),
        "diary_count": len(diary_rows),
        "condition_stats": _group_performance(joined_rows, "condition_type"),
        "grade_stats": _group_performance(joined_rows, "grade"),
        "score_stats": _score_bucket_stats(joined_rows),
        "volume_stats": _volume_bucket_stats(joined_rows),
        "rsi_stats": _rsi_bucket_stats(joined_rows),
        "disparity_stats": _disparity_bucket_stats(joined_rows),
        "day_change_stats": _day_change_bucket_stats(joined_rows),
        "high_score_failures": _high_score_failures(joined_rows),
        "low_score_successes": _low_score_successes(joined_rows),
        "top_outcomes": sorted(joined_rows, key=lambda row: row.get("rate", 0), reverse=True)[:5],
        "bottom_outcomes": sorted(joined_rows, key=lambda row: row.get("rate", 0))[:5],
        "joined_rows": joined_rows,
        "diary_rows": diary_rows,
        "diary_by_date": _diary_by_date(diary_rows),
        "file_stats": [],
        "summary": _performance_summary(joined_rows),
    }
    result["insights"] = _verification_insights(result)
    return result


def _combine_verifications(verifications: list[dict]) -> dict:
    if not verifications:
        raise ValueError("검증할 파일이 없습니다.")

    joined_rows = [row for verification in verifications for row in verification["joined_rows"]]
    diary_rows = [row for verification in verifications for row in verification["diary_rows"]]
    file_stats = [_file_summary(verification) for verification in verifications]

    if len(verifications) == 1:
        verification = verifications[0]
        verification["file_stats"] = file_stats
        verification["insights"] = _verification_insights(verification)
        return verification

    result = {
        "filename": f"{len(verifications)}개 파일 누적",
        "file_count": len(verifications),
        "sheet_name": "누적 검증",
        "raw_count": sum(verification["raw_count"] for verification in verifications),
        "analyzed_count": sum(verification["analyzed_count"] for verification in verifications),
        "outcome_count": sum(verification["outcome_count"] for verification in verifications),
        "diary_count": sum(verification["diary_count"] for verification in verifications),
        "condition_stats": _group_performance(joined_rows, "condition_type"),
        "grade_stats": _group_performance(joined_rows, "grade"),
        "score_stats": _score_bucket_stats(joined_rows),
        "volume_stats": _volume_bucket_stats(joined_rows),
        "rsi_stats": _rsi_bucket_stats(joined_rows),
        "disparity_stats": _disparity_bucket_stats(joined_rows),
        "day_change_stats": _day_change_bucket_stats(joined_rows),
        "high_score_failures": _high_score_failures(joined_rows),
        "low_score_successes": _low_score_successes(joined_rows),
        "top_outcomes": sorted(joined_rows, key=lambda row: row.get("rate", 0), reverse=True)[:10],
        "bottom_outcomes": sorted(joined_rows, key=lambda row: row.get("rate", 0))[:10],
        "joined_rows": joined_rows,
        "diary_rows": diary_rows,
        "diary_by_date": _diary_by_date(diary_rows),
        "file_stats": file_stats,
        "summary": _performance_summary(joined_rows),
    }
    result["insights"] = _verification_insights(result)
    return result


def _file_summary(verification: dict) -> dict:
    summary = verification["summary"]
    return {
        "filename": verification["filename"],
        "condition_type": _dominant_condition(verification["joined_rows"]),
        "analyzed_count": verification["analyzed_count"],
        "matched_count": summary["total"],
        "wins": summary["wins"],
        "losses": summary["losses"],
        "win_rate": summary["win_rate"],
        "average_rate": summary["average_rate"],
    }


def _verification_insights(verification: dict) -> list[dict[str, str]]:
    summary = verification["summary"]
    total = summary["total"]
    insights: list[dict[str, str]] = []

    if total == 0:
        return [
            {
                "type": "warning",
                "title": "아직 결과 매칭이 없습니다",
                "body": "오른쪽 다음날 결과 구역을 채운 뒤 다시 업로드하면 조건별 성과를 해석할 수 있습니다.",
            }
        ]

    if total < 30:
        insights.append(
            {
                "type": "caution",
                "title": "표본이 아직 적습니다",
                "body": f"현재 결과 매칭은 {total}개입니다. 방향은 참고하되, 최소 50개 이상 누적되면 조건 판단이 더 안정적입니다.",
            }
        )

    if summary["average_rate"] > 0:
        insights.append(
            {
                "type": "good",
                "title": "전체 평균은 플러스입니다",
                "body": f"누적 승률은 {summary['win_rate']:.1f}%, 평균 수익률은 {_format_percent(summary['average_rate'])}입니다.",
            }
        )
    else:
        insights.append(
            {
                "type": "warning",
                "title": "전체 평균은 아직 약합니다",
                "body": f"누적 승률은 {summary['win_rate']:.1f}%, 평균 수익률은 {_format_percent(summary['average_rate'])}입니다. 후보 추출보다 2차 선별 조건을 더 봐야 합니다.",
            }
        )

    insight_targets = [
        ("조건식", verification["condition_stats"]),
        ("점수", verification["score_stats"]),
        ("RSI", verification["rsi_stats"]),
        ("이격도", verification["disparity_stats"]),
        ("당일 등락률", verification["day_change_stats"]),
        ("거래량 비율", verification["volume_stats"]),
    ]
    for label, stats in insight_targets:
        best = _best_stat_group(stats, total)
        if best:
            tone = "good" if best["average_rate"] > 0 else "caution"
            insights.append(
                {
                    "type": tone,
                    "title": f"{label} 우세 구간",
                    "body": (
                        f"{best['name']} 구간이 표본 {best['count']}개 중 승률 {best['win_rate']:.1f}%, "
                        f"평균 {_format_percent(best['average_rate'])}로 가장 좋았습니다."
                    ),
                }
            )

    score_80 = _stat_by_name(verification["score_stats"], "80점 이상")
    score_65 = _stat_by_name(verification["score_stats"], "65~79점")
    if _usable_stat(score_80, total) and _usable_stat(score_65, total):
        if score_80["average_rate"] < score_65["average_rate"]:
            insights.append(
                {
                    "type": "warning",
                    "title": "고점수 구간 재검토",
                    "body": (
                        f"80점 이상 평균({_format_percent(score_80['average_rate'])})보다 "
                        f"65~79점 평균({_format_percent(score_65['average_rate'])})이 더 좋습니다. "
                        "높은 점수에 이미 오른 종목이 섞이는지 확인해보세요."
                    ),
                }
            )

    worst = _worst_stat_group(verification["day_change_stats"], total)
    if worst and worst["average_rate"] < 0:
        insights.append(
            {
                "type": "warning",
                "title": "주의할 당일 등락률 구간",
                "body": (
                    f"{worst['name']} 구간은 표본 {worst['count']}개, 평균 {_format_percent(worst['average_rate'])}입니다. "
                    "이 구간이 계속 약하면 영웅문 조건식이나 2차 필터에서 줄이는 후보가 됩니다."
                ),
            }
        )

    if verification["high_score_failures"]:
        insights.append(
            {
                "type": "caution",
                "title": "높은 점수 실패 종목 확인",
                "body": f"높은 점수였지만 하락한 종목이 {len(verification['high_score_failures'])}개 있습니다. 당일 등락률, 거래량 비율, RSI 구간을 같이 비교해보세요.",
            }
        )

    if verification["low_score_successes"]:
        insights.append(
            {
                "type": "good",
                "title": "낮은 점수 성공 종목 확인",
                "body": f"낮은 점수였지만 상승한 종목이 {len(verification['low_score_successes'])}개 있습니다. 점수 로직이 놓치는 반등형 조건이 있는지 볼 만합니다.",
            }
        )

    basic = _stat_by_name(verification["condition_stats"], "기본형")
    precise = _stat_by_name(verification["condition_stats"], "정밀형")
    if _usable_stat(basic, total) and _usable_stat(precise, total):
        better, weaker = (basic, precise) if basic["average_rate"] >= precise["average_rate"] else (precise, basic)
        better_note = _condition_summary(better["name"])
        weaker_note = _condition_summary(weaker["name"])
        insights.append(
            {
                "type": "good" if better["average_rate"] > 0 else "caution",
                "title": "조건식 비교",
                "body": (
                    f"현재 누적 기준으로 {better['name']} 평균({_format_percent(better['average_rate'])})이 "
                    f"{weaker['name']} 평균({_format_percent(weaker['average_rate'])})보다 좋습니다. "
                    f"{better_note} {weaker['name']}은 {weaker_note}"
                ),
            }
        )

    return insights[:10]


def _best_stat_group(stats: list[dict], total: int) -> dict | None:
    usable = [stat for stat in stats if _usable_stat(stat, total)]
    if not usable:
        return None
    return max(usable, key=lambda stat: (stat["average_rate"], stat["win_rate"]))


def _worst_stat_group(stats: list[dict], total: int) -> dict | None:
    usable = [stat for stat in stats if _usable_stat(stat, total)]
    if not usable:
        return None
    return min(usable, key=lambda stat: (stat["average_rate"], stat["win_rate"]))


def _usable_stat(stat: dict | None, total: int) -> bool:
    if not stat:
        return False
    minimum = 2 if total < 30 else 3
    return stat.get("count", 0) >= minimum


def _stat_by_name(stats: list[dict], name: str) -> dict | None:
    return next((stat for stat in stats if stat["name"] == name), None)


def _format_percent(value: float) -> str:
    return f"{value:+.2f}%"


def _condition_summary(name: str) -> str:
    preset = CONDITION_PRESETS.get(name)
    if not preset:
        return "조건식 상세 내용이 등록되어 있지 않습니다."
    return preset["summary"]


def _read_xlsx_rows(content: bytes) -> list[tuple[str, list[list[str]]]]:
    workbook_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    ns = {"a": workbook_ns}
    rel_tag = f"{{{rel_ns}}}id"

    with ZipFile(BytesIO(content)) as archive:
        names = set(archive.namelist())
        shared_strings = _read_shared_strings(archive, names, ns)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall(f"{{{package_rel_ns}}}Relationship")}

        sheets = []
        for sheet in workbook.findall(".//a:sheet", {"a": workbook_ns, "r": rel_ns}):
            sheet_name = sheet.attrib.get("name", "Sheet")
            target = relmap.get(sheet.attrib.get(rel_tag, ""))
            if not target:
                continue
            sheet_path = target.lstrip("/")
            if not sheet_path.startswith("xl/"):
                sheet_path = f"xl/{sheet_path}"
            root = ET.fromstring(archive.read(sheet_path))
            sheets.append((sheet_name, _read_sheet_rows(root, shared_strings, ns)))
        return sheets


def _read_shared_strings(archive: ZipFile, names: set[str], ns: dict[str, str]) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(text.text or "" for text in item.findall(".//a:t", ns)) for item in root.findall("a:si", ns)]


def _read_sheet_rows(root: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> list[list[str]]:
    rows = []
    for row in root.findall(".//a:sheetData/a:row", ns):
        values: list[str] = []
        for cell in row.findall("a:c", ns):
            index = _cell_column_index(cell.attrib.get("r", "A1"))
            while len(values) <= index:
                values.append("")
            values[index] = _cell_value(cell, shared_strings, ns)
        while values and values[-1] == "":
            values.pop()
        if values:
            rows.append(values)
    return rows


def _cell_value(cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("a:v", ns)
    if cell_type == "s" and value is not None and value.text:
        index = int(value.text)
        return shared_strings[index] if index < len(shared_strings) else ""
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//a:t", ns))
    return value.text if value is not None and value.text else ""


def _cell_column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    total = 0
    for char in letters:
        total = total * 26 + (ord(char.upper()) - ord("A") + 1)
    return total - 1


def _extract_verification_rows(rows: list[list[str]]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    raw_rows: list[dict] = []
    analyzed_rows: list[dict] = []
    outcome_rows: list[dict] = []
    diary_rows: list[dict] = []
    condition_index = _header_index(rows, "조건식")
    file_condition = _file_condition_type(rows, condition_index)

    for row in rows[1:]:
        if len(row) >= 7 and _cell(row, 0).isdigit():
            raw_rows.append(
                {
                    "code": row[0],
                    "raw_name": row[1],
                    "current_price": _safe_int(row[2]) or 0,
                    "day_direction": row[3],
                    "day_change": _safe_int(row[4]) or 0,
                    "day_rate": _rate_percent(row[5]),
                    "raw_volume": _safe_int(row[6]) or 0,
                }
            )

        if len(row) >= 16 and _safe_int(row[11]) is not None and row[12]:
            code = _extract_code(row[8]) or _cell(row, 0)
            condition_type = _normalize_condition_type(_cell(row, condition_index)) if condition_index is not None else ""
            analyzed_rows.append(
                {
                    "code": code,
                    "condition_type": condition_type or file_condition,
                    "name": _clean_name_code(row[8], code) or _cell(row, 1),
                    "close": _cell(row, 9),
                    "before_score": _safe_int(row[10]),
                    "final_score": _safe_int(row[11]) or 0,
                    "grade": row[12],
                    "vol_ratio": _first_float(row[13]),
                    "rsi": _first_float(row[14]),
                    "disparity": _first_float(row[15]),
                }
            )

        outcome_start = _outcome_start_index(row)
        if outcome_start is not None:
            base_price = _safe_int(_cell(row, outcome_start + 2))
            after_price = _safe_int(_cell(row, outcome_start + 3))
            diff = _safe_int(_cell(row, outcome_start + 4))
            outcome_rows.append(
                {
                    "code": _cell(row, outcome_start),
                    "name": _cell(row, outcome_start + 1),
                    "base": base_price,
                    "after": after_price,
                    "diff": diff,
                    "status": _cell(row, outcome_start + 5),
                    "rate": _rate_percent(_cell(row, outcome_start + 6)),
                }
            )

        if len(row) >= 6 and _cell(row, 1).startswith("2026."):
            diary_rows.append(
                {
                    "date": row[1],
                    "name": row[2],
                    "shares": _safe_int(row[3]) or 0,
                    "price": _safe_int(row[4]) or 0,
                    "total": _safe_int(row[5]) or 0,
                }
            )

    return raw_rows, analyzed_rows, outcome_rows, diary_rows


def _join_analysis_and_outcomes(analyzed_rows: list[dict], outcome_rows: list[dict], raw_rows: list[dict]) -> list[dict]:
    outcomes_by_code = {row["code"]: row for row in outcome_rows if row.get("code")}
    raw_by_code = {row["code"]: row for row in raw_rows if row.get("code")}
    joined_rows = []
    for row in analyzed_rows:
        outcome = outcomes_by_code.get(row.get("code"))
        if not outcome:
            continue
        joined_rows.append({**raw_by_code.get(row.get("code"), {}), **row, **outcome})
    return joined_rows


def _performance_summary(rows: list[dict]) -> dict:
    total = len(rows)
    wins = sum(1 for row in rows if row.get("rate", 0) > 0)
    flats = sum(1 for row in rows if row.get("rate", 0) == 0)
    losses = sum(1 for row in rows if row.get("rate", 0) < 0)
    average = sum(row.get("rate", 0) for row in rows) / total if total else 0
    return {
        "total": total,
        "wins": wins,
        "flats": flats,
        "losses": losses,
        "win_rate": (wins / total * 100) if total else 0,
        "average_rate": average,
    }


def _group_performance(rows: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key, "-")), []).append(row)
    return [_summarize_group(name, items) for name, items in groups.items()]


def _score_bucket_stats(rows: list[dict]) -> list[dict]:
    buckets = [
        ("80점 이상", lambda row: row["final_score"] >= 80),
        ("65~79점", lambda row: 65 <= row["final_score"] < 80),
        ("50~64점", lambda row: 50 <= row["final_score"] < 65),
        ("50점 미만", lambda row: row["final_score"] < 50),
    ]
    return [_summarize_group(name, [row for row in rows if predicate(row)]) for name, predicate in buckets]


def _volume_bucket_stats(rows: list[dict]) -> list[dict]:
    buckets = [
        ("2.0배 이상", lambda row: row["vol_ratio"] >= 2),
        ("1.5~2.0배", lambda row: 1.5 <= row["vol_ratio"] < 2),
        ("1.0~1.5배", lambda row: 1 <= row["vol_ratio"] < 1.5),
        ("1.0배 미만", lambda row: row["vol_ratio"] < 1),
    ]
    return [_summarize_group(name, [row for row in rows if predicate(row)]) for name, predicate in buckets]


def _rsi_bucket_stats(rows: list[dict]) -> list[dict]:
    buckets = [
        ("50 미만", lambda row: row["rsi"] < 50),
        ("50~55", lambda row: 50 <= row["rsi"] < 55),
        ("55~60", lambda row: 55 <= row["rsi"] < 60),
        ("60~65", lambda row: 60 <= row["rsi"] < 65),
        ("65 이상", lambda row: row["rsi"] >= 65),
    ]
    return [_summarize_group(name, [row for row in rows if predicate(row)]) for name, predicate in buckets]


def _disparity_bucket_stats(rows: list[dict]) -> list[dict]:
    buckets = [
        ("100 미만", lambda row: row["disparity"] < 100),
        ("100~102", lambda row: 100 <= row["disparity"] < 102),
        ("102~105", lambda row: 102 <= row["disparity"] < 105),
        ("105~108", lambda row: 105 <= row["disparity"] < 108),
        ("108 이상", lambda row: row["disparity"] >= 108),
    ]
    return [_summarize_group(name, [row for row in rows if predicate(row)]) for name, predicate in buckets]


def _day_change_bucket_stats(rows: list[dict]) -> list[dict]:
    rows_with_day_rate = [row for row in rows if "day_rate" in row]
    buckets = [
        ("0% 미만", lambda row: row["day_rate"] < 0),
        ("0~3%", lambda row: 0 <= row["day_rate"] < 3),
        ("3~7%", lambda row: 3 <= row["day_rate"] < 7),
        ("7~12%", lambda row: 7 <= row["day_rate"] < 12),
        ("12% 이상", lambda row: row["day_rate"] >= 12),
    ]
    return [_summarize_group(name, [row for row in rows_with_day_rate if predicate(row)]) for name, predicate in buckets]


def _high_score_failures(rows: list[dict]) -> list[dict]:
    failures = [row for row in rows if row.get("final_score", 0) >= 65 and row.get("rate", 0) < 0]
    return sorted(failures, key=lambda row: (row.get("final_score", 0), -row.get("rate", 0)), reverse=True)[:10]


def _low_score_successes(rows: list[dict]) -> list[dict]:
    successes = [
        row
        for row in rows
        if (row.get("final_score", 0) < 50 or row.get("grade") == "매수 부적합") and row.get("rate", 0) > 0
    ]
    return sorted(successes, key=lambda row: row.get("rate", 0), reverse=True)[:10]


def _summarize_group(name: str, rows: list[dict]) -> dict:
    count = len(rows)
    wins = sum(1 for row in rows if row.get("rate", 0) > 0)
    average = sum(row.get("rate", 0) for row in rows) / count if count else 0
    return {
        "name": name,
        "count": count,
        "wins": wins,
        "win_rate": (wins / count * 100) if count else 0,
        "average_rate": average,
    }


def _diary_by_date(rows: list[dict]) -> list[dict]:
    totals: dict[str, int] = {}
    for row in rows:
        totals[row["date"]] = totals.get(row["date"], 0) + row["total"]
    return [{"date": date, "total": total} for date, total in sorted(totals.items())]


def _header_index(rows: list[list[str]], name: str) -> int | None:
    if not rows:
        return None
    return next((index for index, value in enumerate(rows[0]) if str(value).strip() == name), None)


def _file_condition_type(rows: list[list[str]], condition_index: int | None) -> str:
    if condition_index is None:
        return "미지정"
    for row in rows[1:]:
        condition_type = _normalize_condition_type(_cell(row, condition_index))
        if condition_type != "미지정":
            return condition_type
    return "미지정"


def _normalize_condition_type(value: str) -> str:
    text = str(value).strip()
    if not text:
        return "미지정"
    if "기본" in text:
        return "기본형"
    if "정밀" in text:
        return "정밀형"
    return "미지정"


def _dominant_condition(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        condition_type = row.get("condition_type", "미지정")
        counts[condition_type] = counts.get(condition_type, 0) + 1
    if not counts:
        return "미지정"
    return max(counts.items(), key=lambda item: item[1])[0]


def _outcome_start_index(row: list[str]) -> int | None:
    if _cell(row, 17).isdigit():
        return 17
    if _cell(row, 18).isdigit():
        return 18
    return None


def _cell(row: list[str], index: int | None) -> str:
    if index is None:
        return ""
    return row[index] if index < len(row) else ""


def _safe_int(value: str) -> int | None:
    try:
        return int(float(str(value).replace(",", "").replace("원", "").strip()))
    except ValueError:
        return None


def _first_float(value: str) -> float:
    match = re.search(r"-?\d+(?:\.\d+)?(?:E-?\d+)?", str(value), re.IGNORECASE)
    return float(match.group(0)) if match else 0.0


def _rate_percent(value: str) -> float:
    text = str(value)
    rate = _first_float(text)
    return rate if "%" in text else rate * 100


def _source_date_from_filename(filename: str) -> str:
    match = re.search(r"(\d{4})[._-](\d{2})[._-](\d{2})", filename)
    if not match:
        return Path(filename).stem
    return ".".join(match.groups())


def _extract_code(value: str) -> str:
    match = re.search(r"(\d{6})", str(value))
    return match.group(1) if match else ""


def _clean_name_code(value: str, code: str) -> str:
    name = str(value)
    if code:
        name = name.replace(code, "")
    return name.replace(".KS", "").replace(".KQ", "").strip()


if __name__ == "__main__":
    app.run(debug=False, port=5000, use_reloader=False)
