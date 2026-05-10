import unittest

import pandas as pd

from surge_analyzer.analyzer import analyze_many_df, analyze_prices, normalize_symbol


def sample_prices() -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=120, freq="B")
    close = pd.Series([100 + i * 0.35 for i in range(120)], index=index, dtype=float)
    close.iloc[-5:] = [135, 136, 138, 139, 142]
    open_ = close - 0.7
    high = close + 1.2
    low = close - 1.4
    volume = pd.Series([1_000_000] * 115 + [1_100_000, 1_200_000, 1_350_000, 1_450_000, 1_900_000], index=index)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


class AnalyzerTests(unittest.TestCase):
    def test_normalize_korean_code_defaults_to_kospi(self):
        self.assertEqual(normalize_symbol("005930"), "005930.KS")

    def test_normalize_preserves_kosdaq_suffix(self):
        self.assertEqual(normalize_symbol("035720.kq"), "035720.KQ")

    def test_normalize_preserves_us_ticker(self):
        self.assertEqual(normalize_symbol("aapl"), "AAPL")

    def test_analysis_contains_requested_sections_and_scores(self):
        result = analyze_prices("TEST", "TEST", sample_prices())

        self.assertFalse(result.is_error)
        self.assertEqual(len(result.score_items), 6)
        self.assertLessEqual(result.final_score, 100)
        self.assertIn(result.final_grade, {"급등 후보 강함", "관심종목", "관망", "매수 부적합"})
        self.assertIn("MA5", result.metrics)
        self.assertIn("VOL_RATIO", result.metrics)

    def test_dataframe_result_shape(self):
        df = analyze_many_df([])
        self.assertIn("최종점수", df.columns)


if __name__ == "__main__":
    unittest.main()
