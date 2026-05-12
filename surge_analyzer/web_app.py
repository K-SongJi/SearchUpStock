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
    uploaded = request.files.get("verification_file")
    verification = None
    verification_error = ""

    if not uploaded or not uploaded.filename:
        verification_error = "검증할 엑셀 파일을 선택해주세요."
    else:
        try:
            verification = _verify_uploaded_workbook(uploaded.read(), uploaded.filename)
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


def _verify_uploaded_workbook(content: bytes, filename: str) -> dict:
    rows_by_sheet = _read_xlsx_rows(content)
    if not rows_by_sheet:
        raise ValueError("시트 데이터를 찾지 못했습니다.")

    sheet_name, rows = rows_by_sheet[0]
    raw_rows, analyzed_rows, outcome_rows, diary_rows = _extract_verification_rows(rows)
    joined_rows = _join_analysis_and_outcomes(analyzed_rows, outcome_rows, raw_rows)

    return {
        "filename": filename,
        "sheet_name": sheet_name,
        "raw_count": len(raw_rows),
        "analyzed_count": len(analyzed_rows),
        "outcome_count": len(outcome_rows),
        "diary_count": len(diary_rows),
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
        "summary": _performance_summary(joined_rows),
    }


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
            analyzed_rows.append(
                {
                    "code": code,
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

        if len(row) >= 24 and _cell(row, 17).isdigit():
            base_price = _safe_int(row[19])
            after_price = _safe_int(row[20])
            diff = _safe_int(row[21])
            outcome_rows.append(
                {
                    "code": row[17],
                    "name": row[18],
                    "base": base_price,
                    "after": after_price,
                    "diff": diff,
                    "status": row[22],
                    "rate": _rate_percent(row[23]),
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


def _cell(row: list[str], index: int) -> str:
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
