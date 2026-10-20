"""HTTP representation of dataset draft revisions."""

import re

from fastapi import status

from datapipette.api.problems import ApiProblem

_ETAG = re.compile(r'^"draft-([1-9][0-9]*)"$')


def draft_etag(revision: int) -> str:
    return f'"draft-{revision}"'


def expected_draft_revision(value: str | None) -> int:
    if value is None:
        raise ApiProblem(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            code="draft_revision_required",
            title="Draft revision required",
            detail="Supply the current draft ETag in the If-Match header.",
        )
    match = _ETAG.fullmatch(value)
    if match is None:
        raise ApiProblem(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_draft_revision",
            title="Invalid draft revision",
            detail="The If-Match header is not a valid dataset draft ETag.",
        )
    return int(match.group(1))
