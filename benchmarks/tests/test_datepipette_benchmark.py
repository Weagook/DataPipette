from __future__ import annotations

import unittest

from benchmarks.datepipette_benchmark import (
    FIELD_NAMES,
    Sample,
    benchmark_definition,
    build_scenarios,
    percentile,
    summarize_samples,
)


class BenchmarkDefinitionTests(unittest.TestCase):
    def test_fixture_exposes_exactly_fifty_stable_fields(self) -> None:
        definition = benchmark_definition()

        self.assertEqual(len(FIELD_NAMES), 50)
        self.assertEqual(len(definition["fields"]), 50)
        self.assertEqual(
            [field["name"] for field in definition["fields"]],
            list(FIELD_NAMES),
        )
        self.assertEqual(len({field["id"] for field in definition["fields"]}), 50)
        self.assertEqual(definition["source"]["relations"][0]["name"], "events")

    def test_mixed_workload_uses_published_field_ids(self) -> None:
        exploration = {
            "columns": [
                {"name": name, "field_id": f"field-{index}"}
                for index, name in enumerate(FIELD_NAMES)
            ]
        }

        scenarios = build_scenarios(exploration)

        self.assertEqual(
            [scenario.name for scenario in scenarios],
            [
                "point_lookup",
                "categorical_filter",
                "date_and_amount_range",
                "text_scan",
                "wide_page",
            ],
        )
        self.assertEqual(len(scenarios[-1].payload["fields"]), 50)


class BenchmarkStatisticsTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]

        self.assertEqual(percentile(values, 0.50), 2.0)
        self.assertEqual(percentile(values, 0.95), 4.0)
        self.assertIsNone(percentile([], 0.50))

    def test_summary_separates_success_from_capacity_responses(self) -> None:
        samples = [
            Sample("point_lookup", 200, 10.0, 100),
            Sample("point_lookup", 200, 30.0, 100),
            Sample("wide_page", 503, 2.0, 80),
        ]

        summary = summarize_samples(samples, 2.0)

        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["succeeded"], 2)
        self.assertEqual(summary["status_counts"], {"200": 2, "503": 1})
        self.assertEqual(summary["requests_per_second"], 1.0)
        self.assertEqual(summary["successful_latency_ms"]["p50"], 10.0)
        self.assertEqual(summary["successful_latency_ms"]["p95"], 30.0)
        self.assertEqual(summary["scenarios"]["wide_page"]["succeeded"], 0)


if __name__ == "__main__":
    unittest.main()
