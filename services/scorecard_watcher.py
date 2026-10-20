import logging
import os
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from services.scorecard_service import import_scorecard_excel


logger = logging.getLogger(__name__)


def find_onedrive_folders():
    """Deteksi folder OneDrive dari environment Windows."""
    folders = set()

    for key, value in os.environ.items():
        if key.lower().startswith("onedrive") and value:
            path = os.path.abspath(
                os.path.expandvars(os.path.expanduser(value))
            )

            if os.path.isdir(path):
                folders.add(os.path.normcase(path))

    return sorted(folders)


def find_excel_file(root_folders, filename):
    """Cari file Excel berdasarkan nama di seluruh subfolder."""
    matches = []

    for root_folder in root_folders:
        for root, dirs, files in os.walk(root_folder):
            dirs[:] = [
                directory
                for directory in dirs
                if directory not in {
                    "$RECYCLE.BIN",
                    "System Volume Information",
                    ".git",
                }
            ]

            for file in files:
                if file.lower() == filename.lower():
                    matches.append(os.path.join(root, file))

    if not matches:
        return None

    matches.sort(key=lambda path: (path.count(os.sep), path.lower()))

    if len(matches) > 1:
        logger.warning(
            "Ditemukan beberapa file dengan nama sama: %s. "
            "Menggunakan: %s",
            filename,
            matches[0],
        )

    return os.path.normcase(os.path.abspath(matches[0]))


class ScorecardExcelWatcher:
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
        self.state_lock = threading.Lock()
        self.stopped = False

        # Jumlah percobaan membuka file yang sedang terkunci.
        self.max_read_retries = 6
        self.retry_delay = 3

    def _read_stable_file(self):
        """
        Membaca file setelah ukurannya stabil.
        Menangani file yang sedang dikunci Excel atau OneDrive.
        """
        previous_signature = None
        stable_checks = 0

        for _ in range(30):
            if self.stopped:
                return None

            if not self.file_path or not os.path.isfile(self.file_path):
                return None

            try:
                stat = os.stat(self.file_path)
                signature = (stat.st_size, stat.st_mtime_ns)
            except OSError as exc:
                logger.warning(
                    "Belum dapat memeriksa file Excel: %s",
                    exc,
                )
                time.sleep(1)
                continue

            if signature == previous_signature:
                stable_checks += 1
            else:
                stable_checks = 0

            previous_signature = signature

            if stable_checks >= 2 and stat.st_size > 0:
                break

            time.sleep(1)
        else:
            logger.warning(
                "File Scorecard belum stabil; sinkronisasi ditunda."
            )
            return None

        # Coba membuka file beberapa kali jika terkunci.
        for attempt in range(self.max_read_retries):
            if self.stopped:
                return None

            try:
                with open(self.file_path, "rb") as excel_file:
                    content = excel_file.read()

                # Pastikan file tidak berubah ketika dibaca.
                stat_after = os.stat(self.file_path)

                if (
                    stat_after.st_size != stat.st_size
                    or stat_after.st_mtime_ns != stat.st_mtime_ns
                ):
                    logger.warning(
                        "File berubah saat dibaca. "
                        "Menjadwalkan ulang sinkronisasi."
                    )
                    return None

                if not content:
                    logger.warning("File Excel kosong.")
                    return None

                return content

            except PermissionError:
                logger.warning(
                    "File Excel sedang dikunci atau akses ditolak. "
                    "Percobaan %s/%s; mencoba lagi dalam %s detik.",
                    attempt + 1,
                    self.max_read_retries,
                    self.retry_delay,
                )

            except OSError:
                logger.exception(
                    "Gagal membaca file Excel: %s",
                    self.file_path,
                )

            if attempt < self.max_read_retries - 1:
                time.sleep(self.retry_delay)

        logger.error(
            "Gagal membuka file Excel setelah %s percobaan: %s",
            self.max_read_retries,
            self.file_path,
        )
        return None

    def sync_file(self):
        if self.stopped:
            return

        if not self.lock.acquire(blocking=False):
            logger.info("Sinkronisasi Scorecard masih berjalan.")
            return

        try:
            if not self.file_path or not os.path.isfile(self.file_path):
                self._detach_file()
                self._attach_file()

            if not self.file_path:
                logger.warning(
                    "File Excel '%s' belum ditemukan di OneDrive.",
                    self.filename,
                )
                return

            content = self._read_stable_file()

            if content is None:
                logger.warning(
                    "Sinkronisasi ditunda karena file belum dapat dibaca. "
                    "Menunggu event file berikutnya atau pencarian berkala."
                )
                return

            result = import_scorecard_excel(
                file_bytes=content,
                sales_office=self.sales_office,
                report_period=self.report_period,
                source_file=os.path.basename(self.file_path),
            )

            logger.info("Scorecard berhasil disinkronkan: %s", result)

        except Exception:
            logger.exception(
                "Sinkronisasi Scorecard gagal. Periksa file Excel, "
                "log backend, dan koneksi Supabase development."
            )

            # Coba ulang jika import gagal karena file atau koneksi sementara.
            if not self.stopped:
                self.schedule_sync()

        finally:
            self.lock.release()

    def _attach_file(self):
        folders = find_onedrive_folders()

        if not folders:
            logger.warning(
                "Folder OneDrive tidak terdeteksi. "
                "Pastikan OneDrive aktif dan tersinkron."
            )
            return

        found_path = find_excel_file(folders, self.filename)

        if not found_path:
            logger.warning(
                "File '%s' belum ditemukan di folder OneDrive.",
                self.filename,
            )
            return

        if (
            found_path == self.file_path
            and self.observer is not None
            and self.observer.is_alive()
        ):
            return

        self._detach_file()
        self.file_path = found_path

        folder = os.path.dirname(self.file_path)
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory or watcher.stopped:
                    return

                paths = [event.src_path]

                if hasattr(event, "dest_path") and event.dest_path:
                    paths.append(event.dest_path)

                for path in paths:
                    normalized = os.path.normcase(
                        os.path.abspath(path)
                    )

                    if normalized == watcher.file_path:
                        watcher.schedule_sync()
                        return

        try:
            self.observer = Observer()
            self.observer.schedule(
                Handler(),
                folder,
                recursive=False,
            )
            self.observer.start()

            logger.info(
                "Watcher Scorecard aktif: %s",
                self.file_path,
            )

        except Exception:
            logger.exception("Gagal memasang watcher Excel.")
            self.observer = None
            self.file_path = None

    def _detach_file(self):
        observer = self.observer
        self.observer = None

        if observer is not None:
            observer.stop()

            # Hindari menunggu thread watcher jika dipanggil
            # dari thread observer itu sendiri.
            if threading.current_thread() is not observer:
                observer.join(timeout=5)

        self.file_path = None

    def schedule_sync(self):
        if self.stopped:
            return

        with self.state_lock:
            if self.stopped:
                return

            if self.timer is not None:
                self.timer.cancel()

            self.timer = threading.Timer(
                3,
                self.sync_file,
            )
            self.timer.daemon = True
            self.timer.start()

    def _search_again(self):
        if self.stopped:
            return

        try:
            if (
                not self.file_path
                or not os.path.isfile(self.file_path)
                or self.observer is None
                or not self.observer.is_alive()
            ):
                self._detach_file()
                self._attach_file()

                if self.file_path:
                    self.schedule_sync()

        except Exception:
            logger.exception(
                "Pencarian ulang file Scorecard gagal."
            )

        finally:
            if not self.stopped:
                self.search_timer = threading.Timer(
                    self.search_interval,
                    self._search_again,
                )
                self.search_timer.daemon = True
                self.search_timer.start()

    def start(self):
        self.stopped = False

        self._attach_file()

        if self.file_path:
            logger.info(
                "File Scorecard ditemukan. Menjalankan import awal."
            )
            self.schedule_sync()
        else:
            logger.warning(
                "File belum ditemukan. Backend akan mencari ulang "
                "setiap %s detik.",
                self.search_interval,
            )

        self._search_again()

    def stop(self):
        self.stopped = True

        with self.state_lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None

        if self.search_timer is not None:
            self.search_timer.cancel()
            self.search_timer = None

        self._detach_file()

        logger.info("Watcher Scorecard dihentikan.")