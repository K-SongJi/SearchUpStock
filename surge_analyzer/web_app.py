from __future__ import annotations

import base64
import os
from io import BytesIO
from pathlib import Path

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


@app.get("/")
def index():
    return render_template(
        "index.html",
        symbols="",
        period="1y",
        results=[],
        matched_count=0,
        total_count=0,
        chart_symbol="",
        charts_by_yahoo={},
        standalone_charts=[],
    )


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
        symbols=symbols_text,
        period=period,
        results=results,
        matched_count=matched_count,
        total_count=len(results),
        chart_symbol=chart_symbol,
        charts_by_yahoo=charts_by_yahoo,
        standalone_charts=standalone_charts,
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


if __name__ == "__main__":
    app.run(debug=False, port=5000, use_reloader=False)
