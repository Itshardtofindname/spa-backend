
import hashlib
import logging
import os
import threading
import time
from datetime import datetime

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from services.scorecard_service import import_scorecard_excel


logger = logging.getLogger(__name__)

IGNORED_DIRECTORIES = {
    "$RECYCLE.BIN",
    "System Volume Information",
    ".git",
}

FILE_SYNC_DELAY = 3
FILE_STABILITY_INTERVAL = 1
FILE_STABILITY_CHECKS = 3
FILE_STABILITY_TIMEOUT = 30


def log_time():
    """Waktu lokal untuk log."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_path(path):
    """Menghasilkan path absolut yang konsisten."""
    return os.path.normcase(os.path.abspath(path))


def find_onedrive_folders():
    """Mendeteksi folder OneDrive yang tersedia."""
    folders = set()

    for key, value in os.environ.items():
        if not key.lower().startswith("onedrive") or not value:
            continue

        path = normalize_path(
            os.path.expandvars(os.path.expanduser(value))
        )

        if os.path.isdir(path):
            folders.add(path)

    return sorted(folders)


def find_excel_file(root_folders, filename):
    """Mencari file Excel berdasarkan nama."""
    matches = []

    for root_folder in root_folders:
        for root, directories, files in os.walk(root_folder):
            directories[:] = [
                directory
                for directory in directories
                if directory not in IGNORED_DIRECTORIES
            ]

            for file in files:
                if file.casefold() == filename.casefold():
                    matches.append(
                        normalize_path(os.path.join(root, file))
                    )

    if not matches:
        return None

    matches.sort(
        key=lambda path: (path.count(os.sep), path.casefold())
    )

    if len(matches) > 1:
        logger.warning(
            "[SCORECARD] Ditemukan %s file bernama '%s'. "
            "Menggunakan: %s",
            len(matches),
            filename,
            matches[0],
        )

    return matches[0]


class ScorecardExcelWatcher:
    """Memantau Excel dan menyinkronkan Scorecard ke Supabase."""

    def __init__(
        self,
        filename,
        sales_office,
        report_period,
        search_interval=60,
    ):
        self.filename = filename
        self.sales_office = sales_office
        self.report_period = report_period
        self.search_interval = search_interval

        self.file_path = None
        self.observer = None
        self.timer = None
        self.search_timer = None

        self.lock = threading.Lock()
        self.state_lock = threading.RLock()

        self.stopped = True
        self.last_synced_hash = None

        self.max_read_retries = 6
        self.retry_delay = 3

    # ---------------------------------------------------------
    # File discovery
    # ---------------------------------------------------------

    def _get_configured_path(self):
        configured_path = os.getenv(
            "SCORECARD_FILE_PATH",
            "",
        ).strip()

        if not configured_path:
            return None

        configured_path = configured_path.strip('"').strip("'")

        return normalize_path(
            os.path.expandvars(
                os.path.expanduser(configured_path)
            )
        )

    def _attach_file(self):
        """Menemukan file dan memasang observer."""
        configured_path = self._get_configured_path()

        if configured_path:
            found_path = configured_path

            if not os.path.isfile(found_path):
                logger.warning(
                    "[SCORECARD] File belum ditemukan: %s",
                    found_path,
                )
                return
        else:
            folders = find_onedrive_folders()

            if not folders:
                logger.warning(
                    "[SCORECARD] OneDrive tidak ditemukan dan "
                    "SCORECARD_FILE_PATH belum dikonfigurasi."
                )
                return

            found_path = find_excel_file(
                folders,
                self.filename,
            )

            if not found_path:
                logger.warning(
                    "[SCORECARD] File '%s' tidak ditemukan.",
                    self.filename,
                )
                return

        if (
            found_path == self.file_path
            and self.observer is not None
            and self.observer.is_alive()
        ):
            logger.debug(
                "[SCORECARD] Observer sudah aktif: %s",
                found_path,
            )
            return

        self._detach_file()
        self.file_path = found_path

        try:
            observer = Observer()
            observer.schedule(
                self._create_event_handler(),
                os.path.dirname(found_path),
                recursive=False,
            )
            observer.start()

            self.observer = observer

            logger.info(
                "[SCORECARD] WATCHER AKTIF | file=%s | office=%s "
                "| period=%s | time=%s",
                found_path,
                self.sales_office,
                self.report_period,
                log_time(),
            )

        except Exception:
            logger.exception(
                "[SCORECARD] Gagal memasang observer."
            )
            self.file_path = None
            raise

    def _create_event_handler(self):
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory or watcher.stopped:
                    return

                paths = [event.src_path]
                destination = getattr(event, "dest_path", None)

                if destination:
                    paths.append(destination)

                target_path = watcher.file_path

                if not target_path:
                    return

                for path in paths:
                    if normalize_path(path) != target_path:
                        continue

                    logger.info(
                        "[SCORECARD] FILE EVENT | event=%s | "
                        "file=%s | time=%s",
                        event.event_type,
                        target_path,
                        log_time(),
                    )

                    if event.event_type in {
                        "created",
                        "modified",
                        "moved",
                    }:
                        watcher.schedule_sync()

                    elif event.event_type == "deleted":
                        logger.warning(
                            "[SCORECARD] File yang dipantau terhapus: %s",
                            target_path,
                        )

                    return

        return Handler()

    def _detach_file(self):
        observer = self.observer
        old_path = self.file_path

        self.observer = None
        self.file_path = None

        if observer is None:
            return

        logger.info(
            "[SCORECARD] Menghentikan observer: %s",
            old_path,
        )

        observer.stop()

        if threading.current_thread() is not observer:
            observer.join(timeout=5)

        if observer.is_alive():
            logger.warning(
                "[SCORECARD] Observer belum berhenti sepenuhnya."
            )

    # ---------------------------------------------------------
    # File reading
    # ---------------------------------------------------------

    def _read_stable_file(self):
        path = self.file_path

        if not path:
            return None

        logger.info(
            "[SCORECARD] Memeriksa kestabilan file: %s",
            path,
        )

        deadline = time.monotonic() + FILE_STABILITY_TIMEOUT
        previous_signature = None
        stable_checks = 0
        stable_signature = None

        while time.monotonic() < deadline:
            if self.stopped or path != self.file_path:
                return None

            try:
                stat = os.stat(path)
                signature = (stat.st_size, stat.st_mtime_ns)

                if (
                    stat.st_size > 0
                    and signature == previous_signature
                ):
                    stable_checks += 1
                else:
                    stable_checks = 0

                previous_signature = signature

                if stable_checks >= FILE_STABILITY_CHECKS:
                    stable_signature = signature
                    break

            except OSError as exc:
                logger.warning(
                    "[SCORECARD] File belum dapat diperiksa: %s",
                    exc,
                )

            time.sleep(FILE_STABILITY_INTERVAL)

        if stable_signature is None:
            logger.warning(
                "[SCORECARD] File belum stabil atau kosong "
                "setelah %s detik: %s",
                FILE_STABILITY_TIMEOUT,
                path,
            )
            return None

        logger.info(
            "[SCORECARD] File stabil | size=%s bytes | mtime_ns=%s",
            stable_signature[0],
            stable_signature[1],
        )

        for attempt in range(self.max_read_retries):
            if self.stopped or path != self.file_path:
                return None

            try:
                with open(path, "rb") as excel_file:
                    content = excel_file.read()

                stat_after = os.stat(path)
                signature_after = (
                    stat_after.st_size,
                    stat_after.st_mtime_ns,
                )

                if signature_after != stable_signature:
                    logger.warning(
                        "[SCORECARD] File berubah ketika dibaca. "
                        "Sinkronisasi akan dicoba kembali."
                    )
                    return None

                if not content:
                    logger.warning(
                        "[SCORECARD] File Excel kosong: %s",
                        path,
                    )
                    return None

                logger.info(
                    "[SCORECARD] File berhasil dibaca | bytes=%s",
                    len(content),
                )

                return content

            except OSError as exc:
                logger.warning(
                    "[SCORECARD] Gagal membaca file "
                    "(percobaan %s/%s): %s",
                    attempt + 1,
                    self.max_read_retries,
                    exc,
                )

                if attempt < self.max_read_retries - 1:
                    time.sleep(self.retry_delay)

        logger.error(
            "[SCORECARD] GAGAL MEMBACA FILE setelah %s percobaan: %s",
            self.max_read_retries,
            path,
        )

        return None

    # ---------------------------------------------------------
    # Synchronization
    # ---------------------------------------------------------

    def sync_file(self):
        if self.stopped:
            return

        if not self.lock.acquire(blocking=False):
            logger.info(
                "[SCORECARD] Sinkronisasi sebelumnya masih berjalan."
            )
            return

        retry_needed = False

        try:
            observer_alive = (
                self.observer is not None
                and self.observer.is_alive()
            )

            file_available = (
                self.file_path is not None
                and os.path.isfile(self.file_path)
            )

            if not file_available or not observer_alive:
                logger.info(
                    "[SCORECARD] File atau observer tidak tersedia. "
                    "Mencoba memasang ulang."
                )
                self._detach_file()
                self._attach_file()

            if not self.file_path:
                logger.warning(
                    "[SCORECARD] File belum tersedia: %s",
                    self.filename,
                )
                retry_needed = True
                return

            logger.info(
                "[SCORECARD] MULAI SINKRONISASI | file=%s | "
                "office=%s | period=%s | time=%s",
                self.file_path,
                self.sales_office,
                self.report_period,
                log_time(),
            )

            content = self._read_stable_file()

            if content is None:
                logger.warning(
                    "[SCORECARD] File belum dapat dibaca. "
                    "Akan mencoba kembali."
                )
                retry_needed = True
                return

            content_hash = hashlib.sha256(content).hexdigest()

            logger.info(
                "[SCORECARD] SHA256 file: %s",
                content_hash,
            )

            if content_hash == self.last_synced_hash:
                logger.info(
                    "[SCORECARD] TIDAK ADA PERUBAHAN ISI. "
                    "Impor dilewati."
                )
                return

            previous_hash = self.last_synced_hash

            logger.info(
                "[SCORECARD] PERUBAHAN FILE TERDETEKSI | "
                "previous_hash=%s | new_hash=%s",
                previous_hash or "(belum pernah disinkronkan)",
                content_hash,
            )

            logger.info(
                "[SCORECARD] Mengirim data ke Supabase..."
            )

            started_at = time.monotonic()

            result = import_scorecard_excel(
                file_bytes=content,
                sales_office=self.sales_office,
                report_period=self.report_period,
                source_file=os.path.basename(self.file_path),
            )

            elapsed = time.monotonic() - started_at

            # Simpan hash hanya setelah fungsi impor berhasil.
            self.last_synced_hash = content_hash

            if isinstance(result, dict):
                imported_rows = result.get(
                    "imported_rows",
                    result.get("imported", "tidak dilaporkan"),
                )
                skipped_rows = result.get(
                    "skipped_rows",
                    result.get("skipped", "tidak dilaporkan"),
                )

                logger.info(
                    "[SCORECARD] IMPOR BERHASIL | imported_rows=%s "
                    "| skipped_rows=%s | duration=%.2fs | result=%s",
                    imported_rows,
                    skipped_rows,
                    elapsed,
                    result,
                )
            else:
                logger.info(
                    "[SCORECARD] FUNGSI IMPOR SELESAI | "
                    "duration=%.2fs | result=%r",
                    elapsed,
                    result,
                )

            logger.info(
                "[SCORECARD] SINKRONISASI SELESAI | time=%s",
                log_time(),
            )

        except Exception:
            logger.exception(
                "[SCORECARD] IMPOR GAGAL. Hash tidak diperbarui, "
                "sehingga perubahan dapat dicoba kembali."
            )
            retry_needed = True

        finally:
            self.lock.release()

            if retry_needed and not self.stopped:
                logger.warning(
                    "[SCORECARD] Retry dijadwalkan dalam %s detik.",
                    self.retry_delay,
                )
                self.schedule_sync(self.retry_delay)

    # ---------------------------------------------------------
    # Scheduling
    # ---------------------------------------------------------

    def schedule_sync(self, delay=FILE_SYNC_DELAY):
        with self.state_lock:
            if self.stopped:
                return

            if self.timer is not None:
                self.timer.cancel()

            logger.info(
                "[SCORECARD] Sinkronisasi dijadwalkan dalam %s detik.",
                delay,
            )

            self.timer = threading.Timer(
                delay,
                self.sync_file,
            )
            self.timer.daemon = True
            self.timer.start()

    def _schedule_search(self):
        with self.state_lock:
            if self.stopped:
                return

            if self.search_timer is not None:
                self.search_timer.cancel()

            self.search_timer = threading.Timer(
                self.search_interval,
                self._search_again,
            )
            self.search_timer.daemon = True
            self.search_timer.start()

    def _search_again(self):
        if self.stopped:
            return

        try:
            configured_path = self._get_configured_path()

            if configured_path:
                path_changed = configured_path != self.file_path
                file_available = os.path.isfile(configured_path)

                observer_alive = (
                    self.observer is not None
                    and self.observer.is_alive()
                )

                if path_changed or not observer_alive:
                    logger.info(
                        "[SCORECARD] Memeriksa ulang path/observer."
                    )
                    self._detach_file()

                    if file_available:
                        self._attach_file()

                if self.file_path and file_available:
                    # Pemeriksaan berkala sebagai fallback jika
                    # event sistem file tidak terdeteksi.
                    self.schedule_sync()

            else:
                observer_alive = (
                    self.observer is not None
                    and self.observer.is_alive()
                )

                file_available = (
                    self.file_path is not None
                    and os.path.isfile(self.file_path)
                )

                if not file_available or not observer_alive:
                    logger.info(
                        "[SCORECARD] Mencari ulang file Excel."
                    )
                    self._detach_file()
                    self._attach_file()

                    if self.file_path:
                        self.schedule_sync()

        except Exception:
            logger.exception(
                "[SCORECARD] Pencarian ulang file gagal."
            )

        finally:
            self._schedule_search()

    # ---------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------

    def start(self):
        with self.state_lock:
            if not self.stopped:
                logger.info(
                    "[SCORECARD] Watcher sudah berjalan."
                )
                return

            self.stopped = False

        logger.info(
            "[SCORECARD] MEMULAI WATCHER | filename=%s | "
            "office=%s | period=%s | time=%s",
            self.filename,
            self.sales_office,
            self.report_period,
            log_time(),
        )

        try:
            configured_path = self._get_configured_path()

            if configured_path:
                logger.info(
                    "[SCORECARD] Menggunakan path dari .env: %s",
                    configured_path,
                )
            else:
                logger.info(
                    "[SCORECARD] SCORECARD_FILE_PATH kosong. "
                    "Menggunakan pencarian OneDrive."
                )

            self._attach_file()

            if self.file_path:
                self.schedule_sync(1)
            else:
                logger.warning(
                    "[SCORECARD] File belum ditemukan. "
                    "Pencarian ulang setiap %s detik.",
                    self.search_interval,
                )

            self._schedule_search()

        except Exception:
            logger.exception(
                "[SCORECARD] Watcher gagal dimulai."
            )
            self.stop()
            raise

    def stop(self):
        with self.state_lock:
            if self.stopped:
                return

            self.stopped = True

            timers = (
                self.timer,
                self.search_timer,
            )

            self.timer = None
            self.search_timer = None

            for timer in timers:
                if timer is not None:
                    timer.cancel()

        self._detach_file()

        logger.info(
            "[SCORECARD] WATCHER DIHENTIKAN | time=%s",
            log_time(),
        )
