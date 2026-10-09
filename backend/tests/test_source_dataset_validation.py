"""Focused tests for PostgreSQL plan summarization and advisory classification."""

import pytest

from datepipette.domain.datasets import (
    DatasetPlanSummary,
    DatasetValidationState,
    ValidationFindingCode,
)
from datepipette.infrastructure.dataset_validation import PsycopgSourceDatasetValidator


def test_plan_summary_and_large_result_advisory() -> None:
    plan = PsycopgSourceDatasetValidator._plan_summary(
        (
            [
                {
                    "Plan": {
                        "Node Type": "Seq Scan",
                        "Plan Rows": 1_000_000,
                        "Startup Cost": 0.0,
                        "Total Cost": 42_000.5,
                    }
                }
            ],
        )
    )

    state, findings = PsycopgSourceDatasetValidator._classify(plan)

    assert state is DatasetValidationState.VALID_WITH_WARNINGS
    assert findings[0].code is ValidationFindingCode.LARGE_ESTIMATED_RESULT


def test_small_plan_is_valid_and_missing_plan_is_rejected() -> None:
    state, findings = PsycopgSourceDatasetValidator._classify(
        DatasetPlanSummary("Index Scan", 10, 0.1, 1.5)
    )
    assert state is DatasetValidationState.VALID
    assert findings == ()

    with pytest.raises(ValueError, match="no plan"):
        PsycopgSourceDatasetValidator._plan_summary(None)
    with pytest.raises(ValueError, match="invalid plan"):
        PsycopgSourceDatasetValidator._plan_summary(([],))
