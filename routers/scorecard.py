
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
from services.scorecard_drive_sync import sync_scorecard_from_drive


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
            "2026-10-01",
        ).strip()
    )
except ValueError:
    DEFAULT_REPORT_PERIOD = date(2026, 10, 1)


def verify_import_token(token):
    """Memverifikasi token untuk endpoint impor dan sinkronisasi."""
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


def verify_cron_token(authorization):
    """Memastikan endpoint otomatis hanya dipanggil scheduler berizin."""
    expected = os.getenv("CRON_SECRET")

    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Konfigurasi CRON_SECRET belum tersedia",
        )

    prefix = "Bearer "

    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(
            status_code=401,
            detail="Autentikasi scheduler tidak valid",
        )

    token = authorization[len(prefix):].strip()

    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=401,
            detail="Autentikasi scheduler tidak valid",
        )


def build_month_columns(report_period: date):
    """Membangun nama kolom bulan untuk tahun laporan."""
    return [
        f"{calendar.month_abbr[month]}-{str(report_period.year)[-2:]}"
        for month in range(1, 13)
    ]


@router.get("/sync-drive/cron")
def scheduled_sync_drive_scorecard(
    authorization: Annotated[str | None, Header()] = None,
):
    """Sinkronisasi Google Drive oleh scheduler yang terautentikasi."""
    verify_cron_token(authorization)

    try:
        return sync_scorecard_from_drive(
            sales_office=DEFAULT_SALES_OFFICE,
            report_period=DEFAULT_REPORT_PERIOD.isoformat(),
            dry_run=False,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Sinkronisasi Google Drive gagal: {exc}",
        ) from exc


@router.get("/productivity")
def read_scorecard(
    sales_office: str = Query(
        DEFAULT_SALES_OFFICE,
        min_length=1,
    ),
    report_period: date = Query(DEFAULT_REPORT_PERIOD),
):
    """Mengambil Scorecard berdasarkan kantor dan periode laporan."""
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
        ) from exc


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
    """Mengimpor Excel yang diunggah setelah autentikasi."""
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
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Gagal mengimpor Excel: {exc}",
        ) from exc
    finally:
        await file.close()


@router.post("/sync-drive")
def sync_drive_scorecard(
    x_import_token: Annotated[str | None, Header()] = None,
    sales_office: str = Query(
        DEFAULT_SALES_OFFICE,
        min_length=1,
    ),
    report_period: date = Query(DEFAULT_REPORT_PERIOD),
    dry_run: bool = Query(True),
    file_id: Annotated[str | None, Query(min_length=1)] = None,
):
    """
    Validasi atau sinkronisasi workbook dari Google Drive.

    dry_run=true:
        Mengunduh dan memvalidasi file tanpa impor ke database.

    dry_run=false:
        Mengimpor data ke database. Memerlukan token impor yang valid.

    file_id:
        ID file yang dipilih melalui Google Picker.
        Jika tidak diberikan, service menggunakan file ID dari konfigurasi
        GOOGLE_DRIVE_SCORECARD_FILE_ID.
    """
    verify_import_token(x_import_token)

    if file_id is not None:
        file_id = file_id.strip()

        if not file_id:
            raise HTTPException(
                status_code=400,
                detail="file_id tidak boleh kosong",
            )

    try:
        return sync_scorecard_from_drive(
            sales_office=sales_office,
            report_period=report_period.isoformat(),
            dry_run=dry_run,
            file_id=file_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Sinkronisasi Google Drive gagal: {exc}",
        ) from exc
