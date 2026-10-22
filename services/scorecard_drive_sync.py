
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
    """Mengunduh file Excel dari Google Drive tanpa mengubah database."""
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

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    return {
        "file_id": resolved_file_id,
        "file_name": metadata["name"],
        "modified_time": metadata.get("modifiedTime"),
        "size_bytes": len(file_bytes),
        "md5_checksum": metadata.get("md5Checksum"),
        "sha256": file_hash,
        "file_bytes": file_bytes,
    }


def _build_source_path(file_id, sales_office, report_period):
    """Membuat identitas sumber unik untuk file, kantor, dan periode."""
    return (
        f"google-drive:{file_id}"
        f":office={sales_office}"
        f":period={report_period}"
    )


def sync_scorecard_from_drive(
    sales_office,
    report_period,
    dry_run=True,
    file_id=None,
    db_client=None,
    downloader=None,
):
    """
    Memvalidasi atau menyinkronkan Scorecard dari Google Drive.

    dry_run=True:
        Mengunduh dan memvalidasi file tanpa query atau penulisan DB.

    dry_run=False:
        Memvalidasi file, memeriksa hash, mengimpor data,
        lalu memperbarui status sinkronisasi.
    """
    configured_office = os.getenv(
        "SCORECARD_SALES_OFFICE", ""
    ).strip()

    sales_office = str(sales_office).strip()
    report_period = str(report_period).strip()

    if not sales_office:
        raise ValueError("sales_office tidak boleh kosong.")

    if not report_period:
        raise ValueError("report_period tidak boleh kosong.")

    if configured_office and sales_office != configured_office:
        raise ValueError(
            "sales_office tidak sesuai dengan konfigurasi sumber Drive."
        )

    if file_id is not None:
        file_id = file_id.strip()

        if not file_id:
            raise ValueError("file_id tidak boleh kosong.")

    fetch_file = downloader or download_scorecard_excel

    remote = {}

    source_path = _build_source_path(
        file_id or os.getenv("GOOGLE_DRIVE_SCORECARD_FILE_ID", ""),
        sales_office,
        report_period,
    )

    try:
        # 1. Unduh file dari Google Drive.
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

        # 2. Identitas sumber untuk file, kantor, dan periode.
        source_path = _build_source_path(
            actual_file_id,
            sales_office,
            report_period,
        )

        # 3. Validasi workbook sebelum proses impor.
        validation = import_scorecard_excel(
            file_bytes=remote["file_bytes"],
            sales_office=sales_office,
            report_period=report_period,
            source_file=remote["file_name"],
            dry_run=True,
        )

        # 4. Dry-run tidak melakukan query atau penulisan database.
        if dry_run:
            return {
                **validation,
                "message": (
                    "Validasi berhasil. Tidak ada perubahan database."
                ),
                "dry_run": True,
                "changed": None,
                "sha256": remote["sha256"],
                "file_id": actual_file_id,
                "file_name": remote["file_name"],
                "report_period": report_period,
                "sync_status": "validation_only",
            }

        # 5. Pilih klien database untuk impor aktual.
        client = db_client if db_client is not None else supabase

        # 6. Cari catatan sumber untuk kantor dan periode ini.
        source_rows = (
            client.table("scorecard_sources")
            .select("last_file_hash,sync_status")
            .eq("sales_office", sales_office)
            .eq("report_period", report_period)
            .limit(1)
            .execute()
            .data
            or []
        )

        previous_hash = (
            source_rows[0].get("last_file_hash")
            if source_rows
            else None
        )

        previous_status = (
            source_rows[0].get("sync_status")
            if source_rows
            else None
        )

        # Lewati hanya jika hash sama dan sinkronisasi sebelumnya sukses.
        file_unchanged = (
            previous_hash == remote["sha256"]
            and previous_status == "synced"
        )

        # 7. Impor data aktual.
        import_result = import_scorecard_excel(
            file_bytes=remote["file_bytes"],
            sales_office=sales_office,
            report_period=report_period,
            source_file=remote["file_name"],
            dry_run=False,
            db_client=client,
        )

        # 8. Catat status synced.
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
            **import_result,
            "message": (
                "File tidak berubah; rekonsiliasi database tetap dijalankan."
                if file_unchanged
                else "Sinkronisasi Scorecard berhasil."
            ),
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
        # Catat error hanya untuk proses sinkronisasi aktual.
        if not dry_run:
            try:
                client = db_client if db_client is not None else supabase

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
                # Jangan menutupi error utama dengan error pencatatan.
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
    """
    Memperbarui catatan sumber yang sudah ada untuk kantor dan periode.
    Jika belum ada, buat catatan baru.
    """
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

    # Constraint unik database adalah (sales_office, report_period).
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
        existing_source_path = existing_rows[0].get("source_path")

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
