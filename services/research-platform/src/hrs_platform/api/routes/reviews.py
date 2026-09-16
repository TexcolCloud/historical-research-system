from uuid import UUID

from fastapi import APIRouter, Query

from hrs_platform.api.deps import ReviewDep
from hrs_platform.schemas import (
    ConversionPage,
    DraftReceipt,
    ReviewDecision,
    ReviewDraft,
    ReviewIssue,
    ReviewIssueSummary,
    ReviewReceipt,
)

router = APIRouter(tags=["reviews"])


@router.get("/api/v2/reviews", response_model=list[ReviewIssueSummary])
def review_list(
    review: ReviewDep,
    run_id: UUID | None = None,
    pending: bool = True,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    return review.list(run_id, pending, offset, limit)


@router.get("/api/v2/reviews/{issue_id}", response_model=ReviewIssue)
def review_issue(review: ReviewDep, issue_id: UUID):
    return review.get(issue_id)


@router.get("/api/v2/runs/{run_id}/review-pages/{page}", response_model=ConversionPage)
def review_page(review: ReviewDep, run_id: UUID, page: int):
    return review.page(run_id, page)


@router.post("/api/v2/reviews/{issue_id}/decisions", response_model=ReviewReceipt)
def review_decision(review: ReviewDep, issue_id: UUID, request: ReviewDecision):
    return review.decide(issue_id, request)


@router.put("/api/v2/reviews/{issue_id}/draft", response_model=DraftReceipt)
def review_draft(review: ReviewDep, issue_id: UUID, request: ReviewDraft):
    return review.save_draft(issue_id, request)
