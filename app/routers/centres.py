"""Diagnostic centres and the test catalogue.

Reading is public (anyone can browse centres). Creating/changing needs an admin.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.cache import cache_delete_prefix, cache_get, cache_set
from app.database import get_db
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest, User
from app.pagination import PageParams, paginate
from app.schemas import (
    CentreCreate,
    CentreDetailOut,
    CentreOut,
    CentreTestCreate,
    CentreTestOut,
    IdPath,
    Page,
    TestCreate,
    TestOut,
)
from app.security import get_current_admin

router = APIRouter(tags=["centres & tests"])

# Centres are read far more often than they change, so GETs are cached in
# Redis. Every admin write clears all keys under this prefix, so users never
# see stale data. (The TTL is only a safety net.)
CENTRES_CACHE_PREFIX = "centres:"


def commit_or_conflict(db: Session, message: str) -> None:
    """Commit, turning a UNIQUE-constraint error into 409.

    The "already exists?" checks catch duplicates normally. This covers two
    admins creating the same thing at the same instant.
    """
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, message) from None


# ---------- Test catalogue ----------

@router.get("/tests/", response_model=Page[TestOut])
def list_tests(params: PageParams = Depends(), db: Session = Depends(get_db)):
    return paginate(db, select(DiagnosticTest).order_by(DiagnosticTest.id), params)


@router.post("/tests/", response_model=TestOut, status_code=status.HTTP_201_CREATED)
def create_test(data: TestCreate, db: Session = Depends(get_db), _: User = Depends(get_current_admin)):
    if db.scalar(select(DiagnosticTest).where(DiagnosticTest.name == data.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A test with this name already exists")
    test = DiagnosticTest(**data.model_dump())
    db.add(test)
    commit_or_conflict(db, "A test with this name already exists")
    return test


# ---------- Centres ----------

@router.get("/centres/", response_model=Page[CentreOut])
def list_centres(
    response: Response,
    # Capped because every distinct value becomes its own cache key.
    city: str | None = Query(None, max_length=100),
    params: PageParams = Depends(),
    db: Session = Depends(get_db),
):
    cache_key = f"{CENTRES_CACHE_PREFIX}list:city={(city or '').lower()}:skip={params.skip}:limit={params.limit}"
    if (cached := cache_get(cache_key)) is not None:
        response.headers["X-Cache"] = "HIT"
        return cached

    query = select(DiagnosticCentre).order_by(DiagnosticCentre.id)
    if city:
        # Case-insensitive exact match. (ilike would treat "%" as a wildcard.)
        query = query.where(func.lower(DiagnosticCentre.city) == city.lower())
    page = Page[CentreOut].model_validate(paginate(db, query, params)).model_dump(mode="json")

    cache_set(cache_key, page)
    response.headers["X-Cache"] = "MISS"
    return page


@router.get("/centres/{centre_id}", response_model=CentreDetailOut)
def get_centre(centre_id: IdPath, response: Response, db: Session = Depends(get_db)):
    cache_key = f"{CENTRES_CACHE_PREFIX}detail:{centre_id}"
    if (cached := cache_get(cache_key)) is not None:
        response.headers["X-Cache"] = "HIT"
        return cached

    # selectinload fetches the centre's tests in one extra query instead of
    # one query per test (avoids the "N+1 queries" problem).
    centre = db.scalar(
        select(DiagnosticCentre)
        .where(DiagnosticCentre.id == centre_id)
        .options(selectinload(DiagnosticCentre.tests).selectinload(CentreTest.test))
    )
    if centre is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Centre not found")

    detail = CentreDetailOut.model_validate(centre).model_dump(mode="json")
    cache_set(cache_key, detail)
    response.headers["X-Cache"] = "MISS"
    return detail


@router.post("/centres/", response_model=CentreOut, status_code=status.HTTP_201_CREATED)
def create_centre(data: CentreCreate, db: Session = Depends(get_db), _: User = Depends(get_current_admin)):
    centre = DiagnosticCentre(**data.model_dump())
    db.add(centre)
    db.commit()
    cache_delete_prefix(CENTRES_CACHE_PREFIX)  # the cached lists are now out of date
    return centre


@router.post(
    "/centres/{centre_id}/tests/",
    response_model=CentreTestOut,
    status_code=status.HTTP_201_CREATED,
)
def add_test_to_centre(
    centre_id: IdPath,
    data: CentreTestCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    if db.get(DiagnosticCentre, centre_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Centre not found")
    if db.get(DiagnosticTest, data.test_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test not found")

    exists = db.scalar(
        select(CentreTest).where(CentreTest.centre_id == centre_id, CentreTest.test_id == data.test_id)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "This centre already offers this test")

    centre_test = CentreTest(centre_id=centre_id, test_id=data.test_id, price=data.price)
    db.add(centre_test)
    commit_or_conflict(db, "This centre already offers this test")
    cache_delete_prefix(CENTRES_CACHE_PREFIX)  # this centre's cached detail is now out of date
    return centre_test
