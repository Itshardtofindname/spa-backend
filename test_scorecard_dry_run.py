
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from services.scorecard_service import import_scorecard_excel


FILE_ID = "1SItN5xNdqOPipCp9QFHzwsKbE-G7NTTd"
SERVICE_ACCOUNT_FILE = "google-service-account.json"

SALES_OFFICE = "S003 - SO SERPONG"
REPORT_PERIOD = "2026-09-01"
SOURCE_FILE = "RAPOT Produktifitas S003 - September 2026.xlsx"


def download_excel():
    credentials = (
        service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
    )

    drive = build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )

    request = drive.files().get_media(fileId=FILE_ID)
    buffer = BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return buffer.getvalue()


def main():
    print("Mengunduh Excel dari Google Drive...")
    file_bytes = download_excel()
    print(f"Download selesai: {len(file_bytes):,} bytes")

    print("Menjalankan validasi tanpa akses database...")
    result = import_scorecard_excel(
        file_bytes=file_bytes,
        sales_office=SALES_OFFICE,
        report_period=REPORT_PERIOD,
        source_file=SOURCE_FILE,
        dry_run=True,
    )

    print("\n=== HASIL VALIDASI ===")
    print(f"Status       : {result['message']}")
    print(f"Mode dry-run : {result['dry_run']}")
    print(f"Sheet        : {result['sheet']}")
    print(f"Jumlah data  : {result['sheet_rows_imported']}")
    print(f"Baris dilewati: {result['skipped_rows']}")
    print(f"Kantor       : {result['sales_office']}")
    print(f"Periode      : {result['report_period']}")

    print("\n=== PREVIEW MAKSIMAL 5 SALESMAN ===")
    for record in result["preview"]:
        print(
            f"NPK={record['npk']} | "
            f"Nama={record['salesman']} | "
            f"PH={record['ph']} | "
            f"Bulan={len(record['monthly_values'])}"
        )

    print("\nDRY-RUN SELESAI — tidak ada SELECT atau UPSERT ke Supabase.")


if __name__ == "__main__":
    main()
