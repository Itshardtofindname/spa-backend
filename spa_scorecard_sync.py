
"""Central Sync Agent untuk SPA Scorecard.

Backend mengambil workbook langsung dari Google Drive API.
Agent tidak membaca file lokal, mengakses Supabase, atau menyimpan hash.

Keamanan:
- SCORECARD_AUTO_SYNC=false secara default.
- Saat dinonaktifkan, agent berhenti tanpa menghubungi backend.
- Sinkronisasi aktif hanya jika SCORECARD_AUTO_SYNC=true.
- Tidak dijalankan otomatis saat Windows startup oleh kode ini.
"""

import json
import logging
import os
import time
from datetime import date
from pathlib import Path
from socket import timeout as SocketTimeout
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().with_name(".env"))

LOG = logging.getLogger("spa_scorecard_sync")

DEFAULT_POLL_INTERVAL = 60
MIN_POLL_INTERVAL = 15
HTTP_TIMEOUT = 120


def auto_sync_enabled() -> bool:
    """Sinkronisasi harus diaktifkan secara eksplisit."""
    value = os.getenv("SCORECARD_AUTO_SYNC", "false").strip().lower()

    if value in {"true", "1", "yes", "on"}:
        return True

    if value in {"false", "0", "no", "off", ""}:
        return False

    raise RuntimeError(
        "SCORECARD_AUTO_SYNC harus bernilai true atau false."
    )


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
    backend_url = required_env("SCORECARD_BACKEND_URL").rstrip("/")
    token = required_env("SCORECARD_IMPORT_TOKEN")
    sales_office = required_env("SCORECARD_SALES_OFFICE")
    report_period = required_env("SCORECARD_REPORT_PERIOD")

    # Validasi bahwa ID Drive dikonfigurasi.
    # Backend tetap menggunakan konfigurasi ID Drive miliknya sendiri.
    required_env("GOOGLE_DRIVE_SCORECARD_FILE_ID")

    parsed_url = urlsplit(backend_url)

    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise RuntimeError(
            "SCORECARD_BACKEND_URL tidak valid."
        )

    hostname = parsed_url.hostname.lower()
    is_localhost = hostname in {"127.0.0.1", "::1", "localhost"}

    if parsed_url.scheme == "http" and not is_localhost:
        raise RuntimeError(
            "HTTP hanya diizinkan untuk backend localhost. "
            "Gunakan HTTPS untuk server lain."
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

    if configured_interval < 1:
        raise RuntimeError(
            "SCORECARD_SYNC_POLL_SECONDS harus lebih besar dari nol."
        )

    return {
        "backend_url": backend_url,
        "token": token,
        "sales_office": sales_office,
        "report_period": report_period,
        "poll_interval": max(MIN_POLL_INTERVAL, configured_interval),
    }


def sync_from_drive(config: dict) -> dict:
    """Minta backend memeriksa dan menyinkronkan Google Drive.

    PERHATIAN:
    Endpoint dipanggil dengan dry_run=false. Jika backend menemukan
    perubahan dan validasi berhasil, database dapat diperbarui.
    Fungsi ini hanya boleh dipanggil saat agent diaktifkan eksplisit.
    """
    query = urlencode(
        {
            "sales_office": config["sales_office"],
            "report_period": config["report_period"],
            "dry_run": "false",
        }
    )

    url = f'{config["backend_url"]}/api/scorecard/sync-drive?{query}'

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
        raise RuntimeError(
            f"Backend menolak permintaan sinkronisasi (HTTP {exc.code})."
        ) from exc

    except URLError as exc:
        reason = getattr(exc, "reason", None)

        if isinstance(reason, (TimeoutError, SocketTimeout)):
            raise RuntimeError(
                "Koneksi ke backend melewati batas waktu."
            ) from exc

        raise RuntimeError(
            "Backend tidak dapat dihubungi. "
            "Periksa koneksi dan status backend."
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
    """Catat ringkasan respons tanpa mencetak rahasia."""
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
        LOG.info("File Drive: %s", file_name[:250])

    if dry_run is True:
        LOG.warning(
            "Backend melaporkan dry-run; perubahan database "
            "belum dikonfirmasi."
        )

    if changed is False:
        LOG.info("Backend menyatakan file tidak berubah.")


def run() -> None:
    # PENTING: periksa sakelar sebelum membaca konfigurasi lain
    # dan sebelum melakukan permintaan jaringan apa pun.
    if not auto_sync_enabled():
        LOG.info(
            "Central Sync Agent dinonaktifkan "
            "(SCORECARD_AUTO_SYNC=false). Tidak ada permintaan "
            "ke backend yang dilakukan."
        )
        return

    config = read_config()

    LOG.warning(
        "Central Sync Agent aktif. Sinkronisasi nyata diizinkan."
    )
    LOG.info(
        "Backend=%s | office=%s | period=%s | interval=%ss",
        config["backend_url"],
        config["sales_office"],
        config["report_period"],
        config["poll_interval"],
    )
    LOG.info(
        "Workbook dibaca backend melalui Google Drive API; "
        "agent tidak membaca file Excel lokal."
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
