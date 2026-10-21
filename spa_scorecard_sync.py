
"""Central Sync Agent untuk SPA Scorecard.

Agent memeriksa sinkronisasi melalui backend SPA. Backend mengambil workbook
langsung dari Google Drive API berdasarkan GOOGLE_DRIVE_SCORECARD_FILE_ID.

Agent ini:
- Tidak membaca file Excel dari path lokal.
- Tidak mengakses Supabase secara langsung.
- Tidak mengunduh workbook ke PC.
- Meminta backend memeriksa perubahan setiap interval.
- Tidak menyimpan hash lokal; pemeriksaan hash dilakukan backend.
- Tidak otomatis dijalankan saat Windows startup oleh kode ini.
"""

import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from socket import timeout as SocketTimeout
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv


# Memuat .env dari folder yang sama dengan script.
load_dotenv(Path(__file__).resolve().with_name(".env"))

LOG = logging.getLogger("spa_scorecard_sync")

DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 15
HTTP_TIMEOUT = 120


def required_env(name: str) -> str:
    """Ambil environment variable wajib tanpa mencetak nilainya."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Environment variable {name} belum diatur."
        )
    return value


def read_config() -> dict:
    """Baca dan validasi konfigurasi agent."""
    backend_url = required_env(
        "SCORECARD_BACKEND_URL"
    ).rstrip("/")

    token = required_env("SCORECARD_IMPORT_TOKEN")
    sales_office = required_env("SCORECARD_SALES_OFFICE")
    report_period = required_env("SCORECARD_REPORT_PERIOD")

    # File ID harus dikonfigurasi di backend juga.
    # Agent hanya memeriksa keberadaannya dan tidak mengirimkannya
    # sebagai pengganti konfigurasi Google Drive di backend.
    drive_file_id = required_env(
        "GOOGLE_DRIVE_SCORECARD_FILE_ID"
    )

    if not backend_url.startswith(
        ("https://", "http://127.0.0.1", "http://localhost")
    ):
        raise RuntimeError(
            "SCORECARD_BACKEND_URL harus menggunakan HTTPS, "
            "kecuali backend localhost."
        )

    # Tolak URL localhost yang tidak benar-benar memakai HTTP.
    if backend_url.startswith("http://"):
        if not backend_url.startswith(
            ("http://127.0.0.1", "http://localhost")
        ):
            raise RuntimeError(
                "HTTP hanya diizinkan untuk backend localhost."
            )

    try:
        parsed_period = date.fromisoformat(report_period)
    except ValueError as exc:
        raise RuntimeError(
            "SCORECARD_REPORT_PERIOD harus berformat YYYY-MM-DD."
        ) from exc

    if parsed_period.isoformat() != report_period:
        raise RuntimeError(
            "SCORECARD_REPORT_PERIOD harus berformat YYYY-MM-DD."
        )

    try:
        configured_interval = int(
            os.getenv(
                "SCORECARD_SYNC_POLL_SECONDS",
                str(DEFAULT_POLL_INTERVAL),
            )
        )
    except ValueError as exc:
        raise RuntimeError(
            "SCORECARD_SYNC_POLL_SECONDS harus berupa angka."
        ) from exc

    poll_interval = max(
        MIN_POLL_INTERVAL,
        configured_interval,
    )

    return {
        "backend_url": backend_url,
        "token": token,
        "sales_office": sales_office,
        "report_period": report_period,
        "drive_file_id": drive_file_id,
        "poll_interval": poll_interval,
    }


def sync_from_drive(config: dict) -> dict:
    """Minta backend memeriksa dan menyinkronkan file Google Drive.

    PERHATIAN:
    dry_run=false mengizinkan backend melakukan impor nyata ke database
    jika backend mendeteksi perubahan dan validasi berhasil.
    """
    query = urlencode(
        {
            "sales_office": config["sales_office"],
            "report_period": config["report_period"],
            "dry_run": "false",
        }
    )

    url = (
        f'{config["backend_url"]}'
        f'/api/scorecard/sync-drive?{query}'
    )

    request = Request(
        url,
        data=b"",
        method="POST",
        headers={
            "X-Import-Token": config["token"],
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            status = response.status
            response_text = response.read().decode(
                "utf-8",
                errors="replace",
            )

    except HTTPError as exc:
        # Jangan mencetak isi respons mentah karena dapat memuat
        # detail internal backend.
        raise RuntimeError(
            f"Backend menolak permintaan sinkronisasi "
            f"(HTTP {exc.code})."
        ) from exc

    except URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, SocketTimeout)):
            raise RuntimeError(
                "Koneksi ke backend melewati batas waktu."
            ) from exc

        raise RuntimeError(
            "Backend tidak dapat dihubungi. "
            "Periksa apakah backend lokal sedang berjalan."
        ) from exc

    except (TimeoutError, SocketTimeout) as exc:
        raise RuntimeError(
            "Koneksi ke backend melewati batas waktu."
        ) from exc

    if not 200 <= status < 300:
        raise RuntimeError(
            f"Backend mengembalikan HTTP {status}."
        )

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Respons backend bukan JSON yang valid."
        ) from exc

    if not isinstance(result, dict):
        raise RuntimeError(
            "Format respons sinkronisasi tidak valid."
        )

    return result


def log_sync_result(result: dict) -> None:
    """Catat hasil secara aman tanpa mengasumsikan satu format respons."""
    # Respons backend yang sebenarnya perlu dikonfirmasi dari kode router.
    # Field di bawah bersifat opsional.
    message = result.get("message", "Respons backend diterima")
    changed = result.get("changed")
    dry_run = result.get("dry_run")
    sync_status = result.get("sync_status")
    file_name = result.get("file_name")

    LOG.info("Hasil: %s", str(message)[:250])
    LOG.info(
        "changed=%s | dry_run=%s | sync_status=%s",
        changed,
        dry_run,
        sync_status,
    )

    if isinstance(file_name, str) and file_name:
        LOG.info("File Drive: %s", file_name)

    if dry_run is True:
        LOG.warning(
            "Backend sedang dalam mode dry-run; "
            "perubahan database belum dikonfirmasi."
        )

    if changed is False:
        LOG.info(
            "Backend menyatakan file tidak berubah."
        )


def run() -> None:
    config = read_config()

    LOG.info("Central Sync Agent aktif.")
    LOG.info(
        "Backend=%s | office=%s | period=%s | interval=%ss",
        config["backend_url"],
        config["sales_office"],
        config["report_period"],
        config["poll_interval"],
    )
    LOG.info(
        "File ID Google Drive sudah dikonfigurasi; "
        "agent tidak membaca file lokal."
    )
    LOG.warning(
        "Saat agent berjalan, dry_run=false mengizinkan "
        "impor nyata ke database jika file berubah."
    )

    while True:
        try:
            LOG.info(
                "Meminta backend memeriksa perubahan Google Drive."
            )

            result = sync_from_drive(config)
            log_sync_result(result)

        except KeyboardInterrupt:
            raise

        except Exception as exc:
            # Tidak mencetak URL lengkap, header, token, atau respons mentah.
            LOG.error(
                "Pemeriksaan sinkronisasi gagal: %s",
                str(exc)[:300],
            )
            LOG.info(
                "Agent akan mencoba kembali pada interval berikutnya."
            )

        time.sleep(config["poll_interval"])


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    try:
        run()
    except KeyboardInterrupt:
        LOG.info("Central Sync Agent dihentikan.")
    except Exception as exc:
        LOG.error(
            "Agent tidak dapat dimulai: %s",
            str(exc)[:300],
        )
        raise SystemExit(1)
