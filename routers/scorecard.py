
import os
import secrets
from datetime import date
from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
)

from services.scorecard_service import (
    get_scorecard_productivity,
    import_scorecard_excel,
)

router = APIRouter(
    prefix="/api/scorecard",
    tags=["Scorecard Productivity"],
)


def verify_import_token(token):
    expected = os.getenv("SCORECARD_IMPORT_TOKEN")

    if not expected or not token:
        raise HTTPException(
            status_code=503,
            detail="Konfigurasi token impor belum tersedia",
        )

    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=401,
            detail="Token impor tidak valid",
        )


@router.get("/productivity")
def read_scorecard(
    sales_office: str = Query(
        "S003 - SO SERPONG",
        min_length=1,
    ),
    report_period: date = Query(
        date(2026, 9, 1),
    ),
):
    try:
        rows = get_scorecard_productivity(
            sales_office=sales_office,
            report_period=report_period.isoformat(),
        )

        return {
            "count": len(rows),
            "sales_office": sales_office,
            "report_period": report_period.isoformat(),
            "columns": [
                "npk", "salesman", "ph",
                "Jan-26", "Feb-26", "Mar-26", "Q1",
                "Apr-26", "May-26", "Jun-26",
                "Jul-26", "Aug-26", "Sep-26",
                "perform", "oa", "r_order",
                "cross_sell", "dks",
            ],
            "data": rows,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gagal mengambil Scorecard: {exc}",
        )


@router.post("/import-excel")
async def upload_scorecard_excel(
    file: Annotated[UploadFile, File(...)],
    x_import_token: Annotated[
        str | None, Header()
    ] = None,
    sales_office: str = Query(
        "S003 - SO SERPONG",
        min_length=1,
    ),
    report_period: date = Query(
        date(2026, 9, 1),
    ),
):
    verify_import_token(x_import_token)

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=400,
            detail="File harus berformat .xlsx",
        )

    try:
        content = await file.read()

        if not content:
            raise HTTPException(
                status_code=400,
                detail="File Excel kosong",
            )

        return import_scorecard_excel(
            file_bytes=content,
            sales_office=sales_office,
            report_period=report_period.isoformat(),
            source_file=file.filename,
        )

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gagal mengimpor Excel: {exc}",
        )
