
import os
import secrets
import calendar
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

DEFAULT_SALES_OFFICE = os.getenv(
    "SCORECARD_SALES_OFFICE",
    "S003 - SO SERPONG",
).strip()

try:
    DEFAULT_REPORT_PERIOD = date.fromisoformat(
        os.getenv(
            "SCORECARD_REPORT_PERIOD",
            "2026-09-01",
        ).strip()
    )
except ValueError:
    DEFAULT_REPORT_PERIOD = date(2026, 9, 1)


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


def build_month_columns(report_period: date):
    return [
        f"{calendar.month_abbr[month]}-{str(report_period.year)[-2:]}"
        for month in range(1, 13)
    ]


@router.get("/productivity")
def read_scorecard(
    sales_office: str = Query(
        DEFAULT_SALES_OFFICE,
        min_length=1,
    ),
    report_period: date = Query(DEFAULT_REPORT_PERIOD),
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
                "npk",
                "salesman",
                "ph",
                *build_month_columns(report_period),
                "q1",
                "perform",
                "oa",
                "r_order",
                "cross_sell",
                "dks",
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
    x_import_token: Annotated[str | None, Header()] = None,
    sales_office: str = Query(
        DEFAULT_SALES_OFFICE,
        min_length=1,
    ),
    report_period: date = Query(DEFAULT_REPORT_PERIOD),
):
    verify_import_token(x_import_token)

    if (
        not file.filename
        or not file.filename.lower().endswith(".xlsx")
    ):
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
    finally:
        await file.close()
