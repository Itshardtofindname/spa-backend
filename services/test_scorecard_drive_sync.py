
from services.scorecard_drive_sync import download_scorecard_excel


def main():
    print("Mengunduh metadata dan Excel dari Google Drive...")

    result = download_scorecard_excel(
        file_id="1SItN5xNdqOPipCp9QFHzwsKbE-G7NTTd"
    )

    print("\n=== HASIL DOWNLOAD ===")
    print("Nama file :", result["file_name"])
    print("Ukuran    :", result["size_bytes"], "bytes")
    print("Modified  :", result["modified_time"])
    print("SHA-256   :", result["sha256"])

    print("\nDOWNLOAD BERHASIL.")
    print("Database tidak diakses oleh tes ini.")


if __name__ == "__main__":
    main()
