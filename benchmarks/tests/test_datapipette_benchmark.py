from __future__ import annotations

import random
import unittest

from benchmarks.datapipette_benchmark import (
    FIELD_NAMES,
    Sample,
    benchmark_definition,
    build_scenarios,
    dashboard_selection,
    items_for_selection,
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

    def test_fixture_can_isolate_width_scaling(self) -> None:
        definition = benchmark_definition(20)

        self.assertEqual(len(definition["fields"]), 20)
        self.assertEqual(definition["fields"][-1]["name"], "latency_ms")

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


FILTERS = [
    {
        "id": "region",
        "kind": "values",
        "targets": [{"item_id": "a", "field_id": "f1"}, {"item_id": "b", "field_id": "f2"}],
    },
    {"id": "ordered", "kind": "date_range", "targets": [{"item_id": "a", "field_id": "f3"}]},
]


class DashboardWorkloadTests(unittest.TestCase):
    filters = FILTERS

    def test_selections_use_listed_values_and_whole_months(self) -> None:
        rng = random.Random(7)
        seen: set[str] = set()
        for _ in range(200):
            for selection in dashboard_selection(
                self.filters, {"region": ["North", "South", "West"]}, rng
            ):
                seen.add(selection["filter_id"])
                if selection["filter_id"] == "region":
                    self.assertTrue(1 <= len(selection["values"]) <= 2)
                    self.assertTrue(set(selection["values"]) <= {"North", "South", "West"})
                else:
                    self.assertTrue(selection["from"].endswith("-01"))
                    self.assertLessEqual(selection["from"], selection["to"])
        self.assertEqual(seen, {"region", "ordered"})

    def test_each_item_receives_only_filters_that_target_it(self) -> None:
        selections = [
            {"filter_id": "region", "values": ["North"]},
            {"filter_id": "ordered", "from": "2026-01-01", "to": "2026-01-31"},
        ]
        self.assertEqual(items_for_selection("a", self.filters, selections), selections)
        self.assertEqual(items_for_selection("b", self.filters, selections), selections[:1])
        self.assertEqual(items_for_selection("c", self.filters, selections), [])


if __name__ == "__main__":
    unittest.main()
