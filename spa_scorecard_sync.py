"""Central Sync Agent untuk SPA Scorecard.

Jalankan hanya pada satu PC Windows yang selalu menyala dan memiliki
Google Drive for Desktop. Agent memantau satu file .xlsx yang dikonfigurasi
secara eksplisit lalu mengirimnya ke endpoint backend yang terlindungi.
Agent tidak mengakses Supabase secara langsung.
"""
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import load_dotenv

# Muat .env yang berada di folder script, tanpa menampilkan nilai rahasia.
load_dotenv(Path(__file__).resolve().with_name(".env"))

LOG = logging.getLogger("spa_scorecard_sync")
POLL_INTERVAL = max(5, int(os.getenv("SCORECARD_SYNC_POLL_SECONDS", "10")))
STABILITY_INTERVAL = 2
STABILITY_REPEATS = 3
STABILITY_TIMEOUT = 90
HTTP_TIMEOUT = 120


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} belum diatur.")
    return value


def read_config() -> dict:
    file_path = Path(required_env("SCORECARD_FILE_PATH")).expanduser()
    backend_url = required_env("SCORECARD_BACKEND_URL").rstrip("/")
    token = required_env("SCORECARD_IMPORT_TOKEN")
    sales_office = required_env("SCORECARD_SALES_OFFICE")
    report_period = required_env("SCORECARD_REPORT_PERIOD")

    if file_path.suffix.lower() != ".xlsx":
        raise RuntimeError("SCORECARD_FILE_PATH harus menunjuk file .xlsx.")
    if not backend_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise RuntimeError(
            "SCORECARD_BACKEND_URL harus menggunakan HTTPS, "
            "kecuali untuk backend localhost."
        )
    if len(report_period) != 10 or report_period[4] != "-" or report_period[7] != "-":
        raise RuntimeError("SCORECARD_REPORT_PERIOD harus berformat YYYY-MM-DD.")

    state_path_value = os.getenv("SCORECARD_SYNC_STATE_PATH", "").strip()
    if state_path_value:
        state_path = Path(state_path_value).expanduser()
    else:
        local_app_data = os.getenv("LOCALAPPDATA")
        state_root = Path(local_app_data) / "SPA" if local_app_data else Path.home() / ".spa"
        state_path = state_root / "scorecard_sync_state.json"

    return {
        "file_path": file_path.resolve(),
        "backend_url": backend_url,
        "token": token,
        "sales_office": sales_office,
        "report_period": report_period,
        "state_path": state_path,
    }


def load_last_hash(state_path: Path) -> str | None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        digest = state.get("sha256")
        return digest if isinstance(digest, str) else None
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("State hash tidak dapat dibaca; file akan diperiksa ulang: %s", exc)
        return None


def save_last_hash(state_path: Path, digest: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps({"sha256": digest}, indent=2),
        encoding="utf-8",
    )
    os.replace(temp_path, state_path)


def read_stable_file(path: Path) -> bytes | None:
    """Baca file hanya setelah ukuran dan waktu modifikasi stabil."""
    deadline = time.monotonic() + STABILITY_TIMEOUT
    previous_signature = None
    stable_repeats = 0

    while time.monotonic() < deadline:
        try:
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)

            if stat.st_size > 0 and signature == previous_signature:
                stable_repeats += 1
            else:
                stable_repeats = 0

            previous_signature = signature

            if stable_repeats >= STABILITY_REPEATS:
                data = path.read_bytes()
                after = path.stat()
                after_signature = (after.st_size, after.st_mtime_ns)
                if data and after_signature == signature:
                    return data

                LOG.info("File berubah saat dibaca; menunggu sampai stabil.")
                previous_signature = None
                stable_repeats = 0

        except FileNotFoundError:
            LOG.warning("File belum ditemukan (mungkin Drive belum tersambung): %s", path)
            return None
        except OSError as exc:
            LOG.warning("File belum dapat dibaca: %s", exc)

        time.sleep(STABILITY_INTERVAL)

    LOG.warning("File tidak stabil dalam %s detik; akan dicoba lagi.", STABILITY_TIMEOUT)
    return None


def build_multipart(filename: str, file_bytes: bytes) -> tuple[str, bytes]:
    boundary = "----SPAScorecard" + uuid4().hex
    safe_filename = Path(filename).name.replace('"', "")
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        (
            'Content-Disposition: form-data; name="file"; '
            f'filename="{safe_filename}"\r\n'
        ).encode("utf-8"),
        b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n",
        file_bytes,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return boundary, body


def upload_file(config: dict, file_bytes: bytes) -> dict:
    query = urlencode({
        "sales_office": config["sales_office"],
        "report_period": config["report_period"],
    })
    url = f'{config["backend_url"]}/api/scorecard/import-excel?{query}'
    boundary, body = build_multipart(config["file_path"].name, file_bytes)

    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Import-Token": config["token"],
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            response_text = response.read().decode("utf-8", errors="replace")
            status = response.status
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Backend menolak impor (HTTP {exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Backend tidak dapat dihubungi: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Koneksi ke backend melewati batas waktu.") from exc

    if not 200 <= status < 300:
        raise RuntimeError(f"Backend mengembalikan HTTP {status}: {response_text}")

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Respons backend bukan JSON yang valid.") from exc

    # Endpoint backend saat ini mengembalikan pesan ini setelah proses upsert selesai.
    if not isinstance(result, dict) or result.get("message") != "Impor Scorecard berhasil":
        raise RuntimeError(f"Backend tidak mengonfirmasi impor berhasil: {result}")

    return result


def run() -> None:
    config = read_config()
    LOG.info("Agent aktif; file yang dipantau: %s", config["file_path"])
    LOG.info(
        "Backend=%s | office=%s | period=%s | interval=%ss",
        config["backend_url"],
        config["sales_office"],
        config["report_period"],
        POLL_INTERVAL,
    )

    last_hash = load_last_hash(config["state_path"])

    while True:
        try:
            if not config["file_path"].is_file():
                LOG.warning("File belum tersedia: %s", config["file_path"])
                time.sleep(POLL_INTERVAL)
                continue

            file_bytes = read_stable_file(config["file_path"])
            if file_bytes is None:
                time.sleep(POLL_INTERVAL)
                continue

            digest = hashlib.sha256(file_bytes).hexdigest()
            if digest == last_hash:
                time.sleep(POLL_INTERVAL)
                continue

            LOG.info("Perubahan file terdeteksi; mengirim workbook ke backend.")
            result = upload_file(config, file_bytes)

            # Hash baru disimpan hanya setelah backend mengonfirmasi impor.
            save_last_hash(config["state_path"], digest)
            last_hash = digest
            LOG.info(
                "Impor dikonfirmasi: rows=%s, matched_npk=%s, matched_name=%s, "
                "new_rows=%s, skipped_rows=%s",
                result.get("sheet_rows_imported", "?"),
                result.get("updated_by_npk", "?"),
                result.get("updated_by_name", "?"),
                result.get("new_rows", "?"),
                result.get("skipped_rows", "?"),
            )

        except KeyboardInterrupt:
            raise
        except Exception:
            # Jangan simpan hash pada kegagalan; file akan dicoba ulang.
            LOG.exception("Pemeriksaan atau impor gagal; akan mencoba kembali.")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        run()
    except KeyboardInterrupt:
        LOG.info("Central Sync Agent dihentikan.")
