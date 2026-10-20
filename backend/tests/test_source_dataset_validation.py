"""Focused tests for PostgreSQL plan summarization and safety classification."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from datapipette.domain.datasets import (
    DatasetRelationStatistics,
    DatasetValidationState,
    QuerySafetyPolicy,
    ValidationFindingCode,
    ValidationSeverity,
)
from datapipette.infrastructure.dataset_validation import PsycopgSourceDatasetValidator


def _explain(plan: dict[str, object]) -> tuple[object, ...]:
    return ([{"Plan": plan}],)


def _validator(policy: QuerySafetyPolicy | None = None) -> PsycopgSourceDatasetValidator:
    return PsycopgSourceDatasetValidator(5, 5, policy)


def test_full_plan_summary_and_explainable_advisories() -> None:
    relation_id = uuid4()
    validator = _validator()
    analysis = validator._analyze_plan(
        _explain(
            {
                "Node Type": "Sort",
                "Plan Rows": 1_000_000,
                "Startup Cost": 10.0,
                "Total Cost": 150_000.5,
                "Plans": [
                    {
                        "Node Type": "Hash Join",
                        "Plan Rows": 800_000,
                        "Startup Cost": 5.0,
                        "Total Cost": 100_000.0,
                        "Plans": [
                            {
                                "Node Type": "Seq Scan",
                                "Schema": "public",
                                "Relation Name": "orders",
                                "Plan Rows": 300_000,
                                "Startup Cost": 0.0,
                                "Total Cost": 20_000.0,
                            }
                        ],
                    }
                ],
            }
        )
    )
    stale_at = datetime(2026, 1, 1, tzinfo=UTC)
    statistics = (
        DatasetRelationStatistics(
            relation_id=relation_id,
            schema_name="public",
            relation_name="orders",
            approximate_rows=2_000_000,
            relation_bytes=10_000_000,
            index_bytes=0,
            visible_index_count=0,
            statistics_updated_at=stale_at,
        ),
    )

    state, findings = validator._classify(
        analysis,
        statistics,
        evaluated_at=stale_at + timedelta(days=31),
    )

    assert analysis.summary.node_count == 3
    assert analysis.summary.join_count == 1
    assert analysis.summary.sort_count == 1
    assert analysis.summary.max_sequential_scan_rows == 300_000
    assert state is DatasetValidationState.VALID_WITH_WARNINGS
    assert {finding.code for finding in findings} == {
        ValidationFindingCode.LARGE_ESTIMATED_RESULT,
        ValidationFindingCode.HIGH_PLANNER_COST,
        ValidationFindingCode.LARGE_SEQUENTIAL_SCAN,
        ValidationFindingCode.NO_VISIBLE_INDEX_FOR_LARGE_SCAN,
        ValidationFindingCode.LARGE_SORT,
        ValidationFindingCode.STALE_RELATION_STATISTICS,
    }
    scan = next(
        finding
        for finding in findings
        if finding.code is ValidationFindingCode.LARGE_SEQUENTIAL_SCAN
    )
    assert scan.relation_id == relation_id
    assert scan.observed_value == 300_000
    assert scan.threshold_value == 250_000
    no_index = next(
        finding
        for finding in findings
        if finding.code is ValidationFindingCode.NO_VISIBLE_INDEX_FOR_LARGE_SCAN
    )
    assert no_index.severity is ValidationSeverity.INFO


def test_policy_can_block_by_rows_cost_or_joins() -> None:
    policy = QuerySafetyPolicy(
        estimated_rows_warning=10,
        estimated_rows_block=20,
        total_cost_warning=10,
        total_cost_block=20,
        join_count_warning=1,
        join_count_block=2,
    )
    validator = _validator(policy)
    analysis = validator._analyze_plan(
        _explain(
            {
                "Node Type": "Nested Loop",
                "Plan Rows": 20,
                "Startup Cost": 0.0,
                "Total Cost": 20.0,
                "Plans": [
                    {
                        "Node Type": "Hash Join",
                        "Plan Rows": 5,
                        "Startup Cost": 0.0,
                        "Total Cost": 5.0,
                    }
                ],
            }
        )
    )

    state, findings = validator._classify(analysis)

    assert state is DatasetValidationState.BLOCKED
    assert len(findings) == 3
    assert all(item.severity is ValidationSeverity.BLOCKED for item in findings)


def test_small_plan_is_valid_and_missing_plan_is_rejected() -> None:
    validator = _validator()
    analysis = validator._analyze_plan(
        _explain(
            {
                "Node Type": "Index Scan",
                "Plan Rows": 10,
                "Startup Cost": 0.1,
                "Total Cost": 1.5,
            }
        )
    )
    state, findings = validator._classify(analysis)
    assert state is DatasetValidationState.VALID
    assert findings == ()

    with pytest.raises(ValueError, match="no plan"):
        validator._plan_summary(None)
    with pytest.raises(ValueError, match="invalid plan"):
        validator._plan_summary(([],))


def test_policy_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="row warning"):
        QuerySafetyPolicy(estimated_rows_warning=10, estimated_rows_block=10)
