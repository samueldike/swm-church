from __future__ import annotations

import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select


async def paginate(
    db: AsyncSession,
    query: Select,
    page: int = 1,
    per_page: int = 12,
) -> dict[str, Any]:
    if page < 1:
        page = 1
    if per_page < 1:
        per_page = 12

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar_one() or 0
    total_pages = max(1, math.ceil(total / per_page))

    if page > total_pages:
        page = total_pages

    offset = (page - 1) * per_page
    result = await db.execute(query.offset(offset).limit(per_page))
    items = list(result.scalars().all())

    base_url = ""
    sep = "&" if "?" in base_url else "?"

    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": f"{base_url}{sep}page={page - 1}" if page > 1 else None,
        "next_url": f"{base_url}{sep}page={page + 1}" if page < total_pages else None,
    }
