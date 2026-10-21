
import base64
import hashlib
import json
import os
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
        decoded = base64.b64decode(credentials_base64).decode("utf-8")
        info = json.loads(decoded)
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=DRIVE_READONLY_SCOPE,
        )

    # Fallback untuk pengembangan lokal.
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
    """
    Mengunduh Excel dan mengembalikan metadata, isi file, dan SHA-256.
    Fungsi ini tidak mengakses atau mengubah database.
    """

    resolved_file_id = (
        file_id or os.getenv("GOOGLE_DRIVE_SCORECARD_FILE_ID")
    )

    if not resolved_file_id:
        raise RuntimeError(
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

    if mime_type != (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ):
        raise ValueError(
            "File Google Drive bukan file Excel .xlsx yang didukung: "
            f"{mime_type}"
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


def sync_scorecard_from_drive(
    sales_office,
    report_period,
    dry_run=True,
    file_id=None,
    db_client=None,
    downloader=None,
):
    """Validasi dan sinkronkan workbook Drive; dry-run tidak menulis DB."""
    configured_office = os.getenv("SCORECARD_SALES_OFFICE", "").strip()
    configured_period = os.getenv("SCORECARD_REPORT_PERIOD", "").strip()
    if configured_office and sales_office.strip() != configured_office:
        raise ValueError("sales_office tidak sesuai dengan konfigurasi sumber Drive.")
    if configured_period and report_period != configured_period:
        raise ValueError("report_period tidak sesuai dengan konfigurasi sumber Drive.")

    client = db_client or supabase
    fetch_file = downloader or download_scorecard_excel
    source_path = f"google-drive:{file_id or os.getenv('GOOGLE_DRIVE_SCORECARD_FILE_ID', '')}"

    try:
        remote = fetch_file(file_id=file_id)
        source_path = f"google-drive:{remote['file_id']}"

        # SELECT ini hanya membaca hash terakhir untuk deteksi perubahan.
        source_rows = (
            client.table("scorecard_sources")
            .select("last_file_hash,sync_status")
            .eq("source_path", source_path)
            .limit(1)
            .execute()
            .data
            or []
        )
        previous_hash = source_rows[0].get("last_file_hash") if source_rows else None
        if (
            previous_hash == remote["sha256"]
            and source_rows[0].get("sync_status") == "synced"
        ):
            return {
                "message": "File tidak berubah; sinkronisasi dilewati",
                "changed": False,
                "dry_run": dry_run,
                "file_name": remote["file_name"],
                "sha256": remote["sha256"],
                "sync_status": source_rows[0].get("sync_status", "synced"),
            }

        validation = import_scorecard_excel(
            file_bytes=remote["file_bytes"],
            sales_office=sales_office,
            report_period=report_period,
            source_file=remote["file_name"],
            dry_run=True,
        )
        if dry_run:
            return {
                **validation,
                "changed": True,
                "sha256": remote["sha256"],
                "file_name": remote["file_name"],
                "sync_status": "pending",
            }

        imported = import_scorecard_excel(
            file_bytes=remote["file_bytes"],
            sales_office=sales_office,
            report_period=report_period,
            source_file=remote["file_name"],
        )
        _write_source_status(
            client, sales_office, report_period, remote,
            source_path, "synced", None,
        )
        return {
            **imported,
            "changed": True,
            "sha256": remote["sha256"],
            "file_name": remote["file_name"],
            "sync_status": "synced",
        }
    except Exception as exc:
        if not dry_run:
            try:
                _write_source_status(
                    client, sales_office, report_period,
                    locals().get("remote", {}), source_path, "error", str(exc),
                )
            except Exception:
                pass
        raise


def _write_source_status(
    client, sales_office, report_period, remote, source_path, status, error
):
    from datetime import datetime, timezone

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
        row["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    client.table("scorecard_sources").upsert(
        row, on_conflict="source_path"
    ).execute()
