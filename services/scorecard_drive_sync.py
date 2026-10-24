
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from database import supabase
from services.scorecard_service import import_scorecard_excel
from services.salesmen_service import import_salesman_summ_excel


DRIVE_READONLY_SCOPE = [
    "https://www.googleapis.com/auth/drive.readonly"
]

XLSX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "spreadsheetml.sheet"
)


def get_drive_credentials():
    """Membuat kredensial Google Drive untuk lokal maupun deployment."""
    credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    credentials_base64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_BASE64")

    if credentials_json:
        info = json.loads(credentials_json)
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=DRIVE_READONLY_SCOPE,
        )

    if credentials_base64:
        decoded = base64.b64decode(
            credentials_base64
        ).decode("utf-8")

        info = json.loads(decoded)
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=DRIVE_READONLY_SCOPE,
        )

    local_credentials = (
        Path(__file__).resolve().parent.parent
        / "google-service-account.json"
    )

    if local_credentials.is_file():
        return service_account.Credentials.from_service_account_file(
            str(local_credentials),
            scopes=DRIVE_READONLY_SCOPE,
        )

    raise RuntimeError(
        "Kredensial Google Drive belum dikonfigurasi. "
        "Atur GOOGLE_SERVICE_ACCOUNT_JSON atau "
        "GOOGLE_SERVICE_ACCOUNT_BASE64."
    )


def download_scorecard_excel(file_id=None):
    """Mengunduh Excel terbaru dari Google Drive tanpa mengubah database."""
    resolved_file_id = (
        file_id or os.getenv("GOOGLE_DRIVE_SCORECARD_FILE_ID")
    )

    if not resolved_file_id:
        raise RuntimeError(
            "File ID tidak diberikan dan "
            "GOOGLE_DRIVE_SCORECARD_FILE_ID belum dikonfigurasi."
        )

    credentials = get_drive_credentials()

    drive = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    metadata = (
        drive.files()
        .get(
            fileId=resolved_file_id,
            fields="id,name,mimeType,modifiedTime,size,md5Checksum",
        )
        .execute()
    )

    mime_type = metadata.get("mimeType", "")

    if mime_type != XLSX_MIME_TYPE:
        raise ValueError(
            "File Google Drive bukan file Excel .xlsx yang didukung. "
            f"MIME type: {mime_type or 'tidak diketahui'}"
        )

    request = drive.files().get_media(fileId=resolved_file_id)

    buffer = BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    file_bytes = buffer.getvalue()

    if not file_bytes:
        raise ValueError("File Excel yang diunduh kosong.")

    return {
        "file_id": metadata["id"],
        "file_name": metadata["name"],
        "modified_time": metadata.get("modifiedTime"),
        "size_bytes": len(file_bytes),
        "md5_checksum": metadata.get("md5Checksum"),
        "sha256": hashlib.sha256(file_bytes).hexdigest(),
        "file_bytes": file_bytes,
    }


def _build_source_path(file_id, sales_office, report_period):
    """Membuat identitas sumber untuk file, kantor, dan periode."""
    return (
        f"google-drive:{file_id}"
        f":office={sales_office}"
        f":period={report_period}"
    )


def _get_source_status(client, sales_office, report_period):
    """Mengambil status sync sebelumnya."""
    rows = (
        client.table("scorecard_sources")
        .select("last_file_hash,sync_status,last_synced_at")
        .eq("sales_office", sales_office)
        .eq("report_period", report_period)
        .limit(1)
        .execute()
        .data
        or []
    )

    return rows[0] if rows else None


def _validate_workbook(
    file_bytes,
    sales_office,
    report_period,
    source_file,
):
    """
    Validasi kedua sheet tanpa perubahan database.
    Keduanya harus lolos sebelum sinkronisasi aktual dimulai.
    """
    productivity_validation = import_scorecard_excel(
        file_bytes=file_bytes,
        sales_office=sales_office,
        report_period=report_period,
        source_file=source_file,
        dry_run=True,
    )

    summ_validation = import_salesman_summ_excel(
        file_bytes=file_bytes,
        sales_office=sales_office,
        report_period=report_period,
        source_file=source_file,
        dry_run=True,
    )

    return {
        "productivity_validation": productivity_validation,
        "summ_validation": summ_validation,
        "productivity_rows_validated": (
            productivity_validation.get("sheet_rows_imported", 0)
        ),
        "summ_rows_validated": (
            summ_validation.get("summ_rows_imported", 0)
        ),
    }


def _import_summ(
    file_bytes,
    sales_office,
    report_period,
    source_file,
    client,
):
    """Menjalankan impor SUMM ke salesmen.summ_history."""
    return import_salesman_summ_excel(
        file_bytes=file_bytes,
        sales_office=sales_office,
        report_period=report_period,
        source_file=source_file,
        dry_run=False,
        db_client=client,
    )


def sync_scorecard_from_drive(
    sales_office,
    report_period,
    dry_run=True,
    file_id=None,
    db_client=None,
    downloader=None,
    force_reconcile=False,
):
    """
    Validasi atau sinkronisasi workbook Excel dari Google Drive.

    dry_run=True:
        Mengunduh dan memvalidasi sheet Produktifitas serta SUMM
        tanpa perubahan database.

    dry_run=False:
        Mengimpor Produktifitas dan SUMM, lalu mencatat status.

    force_reconcile=True:
        Memaksa rekonsiliasi data meskipun hash file tidak berubah.
    """
    configured_office = os.getenv(
        "SCORECARD_SALES_OFFICE", ""
    ).strip()

    sales_office = str(sales_office or "").strip()
    report_period = str(report_period or "").strip()

    if not sales_office:
        raise ValueError("sales_office tidak boleh kosong.")

    if not report_period:
        raise ValueError("report_period tidak boleh kosong.")

    if configured_office and sales_office != configured_office:
        raise ValueError(
            "sales_office tidak sesuai dengan konfigurasi sumber Drive."
        )

    if file_id is not None:
        file_id = str(file_id).strip()
        if not file_id:
            raise ValueError("file_id tidak boleh kosong.")

    configured_file_id = (
        file_id or os.getenv("GOOGLE_DRIVE_SCORECARD_FILE_ID", "")
    )

    if not configured_file_id:
        raise RuntimeError(
            "GOOGLE_DRIVE_SCORECARD_FILE_ID belum dikonfigurasi."
        )

    fetch_file = downloader or download_scorecard_excel
    client = db_client if db_client is not None else supabase

    remote = {}
    source_path = _build_source_path(
        configured_file_id,
        sales_office,
        report_period,
    )

    try:
        # 1. Unduh workbook terbaru.
        remote = fetch_file(file_id=file_id)

        actual_file_id = remote.get("file_id")

        if not actual_file_id:
            raise ValueError(
                "Downloader tidak mengembalikan file_id."
            )

        if not remote.get("file_bytes"):
            raise ValueError(
                "Downloader tidak mengembalikan isi file Excel."
            )

        if not remote.get("file_name"):
            raise ValueError(
                "Downloader tidak mengembalikan nama file."
            )

        if not remote.get("sha256"):
            raise ValueError(
                "Downloader tidak mengembalikan SHA-256 file."
            )

        source_path = _build_source_path(
            actual_file_id,
            sales_office,
            report_period,
        )

        # 2. Validasi kedua sheet sebelum menulis apa pun.
        validation = _validate_workbook(
            file_bytes=remote["file_bytes"],
            sales_office=sales_office,
            report_period=report_period,
            source_file=remote["file_name"],
        )

        if dry_run:
            return {
                **validation,
                "message": (
                    "Validasi kedua sheet berhasil. "
                    "Tidak ada perubahan database."
                ),
                "dry_run": True,
                "changed": None,
                "reconciled": False,
                "sha256": remote["sha256"],
                "file_id": actual_file_id,
                "file_name": remote["file_name"],
                "sales_office": sales_office,
                "report_period": report_period,
                "sync_status": "validation_only",
            }

        # 3. Periksa status sync sebelumnya.
        previous = _get_source_status(
            client,
            sales_office,
            report_period,
        )

        previous_hash = (
            previous.get("last_file_hash") if previous else None
        )
        previous_status = (
            previous.get("sync_status") if previous else None
        )

        file_unchanged = (
            previous_hash == remote["sha256"]
            and previous_status == "synced"
        )

        # 4. Jika file sama dan tidak dipaksa rekonsiliasi,
        #    tetap jalankan impor SUMM. Ini memungkinkan histori SUMM
        #    diperbaiki tanpa mengimpor ulang Produktifitas.
        if file_unchanged and not force_reconcile:
            summ_result = _import_summ(
                file_bytes=remote["file_bytes"],
                sales_office=sales_office,
                report_period=report_period,
                source_file=remote["file_name"],
                client=client,
            )

            return {
                **validation,
                "message": (
                    "File tidak berubah. Produktifitas dilewati; "
                    "SUMM berhasil direkonsiliasi."
                ),
                "productivity_import_skipped": True,
                "summ_import": summ_result,
                "changed": False,
                "reconciled": True,
                "dry_run": False,
                "file_id": actual_file_id,
                "file_name": remote["file_name"],
                "sha256": remote["sha256"],
                "sales_office": sales_office,
                "report_period": report_period,
                "sync_status": "synced",
                "last_synced_at": previous.get("last_synced_at"),
            }

        # 5. Impor Produktifitas melalui importer yang sudah ada.
        productivity_result = import_scorecard_excel(
            file_bytes=remote["file_bytes"],
            sales_office=sales_office,
            report_period=report_period,
            source_file=remote["file_name"],
            dry_run=False,
            db_client=client,
        )

        # 6. Impor SUMM ke salesmen.summ_history.
        summ_result = _import_summ(
            file_bytes=remote["file_bytes"],
            sales_office=sales_office,
            report_period=report_period,
            source_file=remote["file_name"],
            client=client,
        )

        # 7. Catat status hanya setelah kedua impor selesai.
        _write_source_status(
            client=client,
            sales_office=sales_office,
            report_period=report_period,
            remote=remote,
            source_path=source_path,
            status="synced",
            error=None,
        )

        return {
            "message": "Sinkronisasi Produktifitas dan SUMM berhasil.",
            "productivity_import": productivity_result,
            "summ_import": summ_result,
            "changed": not file_unchanged,
            "reconciled": True,
            "dry_run": False,
            "file_id": actual_file_id,
            "file_name": remote["file_name"],
            "sha256": remote["sha256"],
            "sales_office": sales_office,
            "report_period": report_period,
            "sync_status": "synced",
        }

    except Exception as exc:
        # Catat error hanya untuk sinkronisasi aktual.
        if not dry_run:
            try:
                _write_source_status(
                    client=client,
                    sales_office=sales_office,
                    report_period=report_period,
                    remote=remote,
                    source_path=source_path,
                    status="error",
                    error=str(exc),
                )
            except Exception:
                # Jangan mengganti exception utama.
                pass

        raise


def _write_source_status(
    client,
    sales_office,
    report_period,
    remote,
    source_path,
    status,
    error,
):
    """Insert atau update status sumber berdasarkan kantor dan periode."""
    row = {
        "source_name": "Google Drive Scorecard",
        "file_name": remote.get("file_name") or source_path,
        "source_path": source_path,
        "sales_office": sales_office,
        "report_period": report_period,
        "is_active": True,
        "sync_status": status,
        "last_error": error,
    }

    if remote.get("sha256"):
        row["last_file_hash"] = remote["sha256"]

    if status == "synced":
        row["last_synced_at"] = datetime.now(
            timezone.utc
        ).isoformat()

    existing_rows = (
        client.table("scorecard_sources")
        .select("source_path")
        .eq("sales_office", sales_office)
        .eq("report_period", report_period)
        .limit(1)
        .execute()
        .data
        or []
    )

    if existing_rows:
        (
            client.table("scorecard_sources")
            .update(row)
            .eq("sales_office", sales_office)
            .eq("report_period", report_period)
            .execute()
        )
    else:
        (
            client.table("scorecard_sources")
            .insert(row)
            .execute()
        )
