from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


class PageParams:
    """Dependency for ?skip=0&limit=20 query parameters."""

    # Both are capped: a huge skip would overflow the database's OFFSET (-> 500).
    def __init__(self, skip: int = Query(0, ge=0, le=100_000), limit: int = Query(20, ge=1, le=100)):
        self.skip = skip
        self.limit = limit


def paginate(db: Session, query: Select, params: PageParams) -> dict:
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.offset(params.skip).limit(params.limit)).all()
    return {"items": items, "total": total, "skip": params.skip, "limit": params.limit}
