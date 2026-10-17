
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
            # Abaikan folder sampah dan metadata OneDrive.
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

    if len(matches) > 1:
        logger.warning(
            "Ditemukan beberapa file dengan nama sama: %s. "
            "Menggunakan file pertama: %s",
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

    def sync_file(self):
        if self.stopped:
            return

        if not self.lock.acquire(blocking=False):
            logger.info("Sinkronisasi Scorecard masih berjalan.")
            return

        try:
            # Cari ulang file jika belum ditemukan atau sudah dipindahkan.
            if not self.file_path or not os.path.isfile(self.file_path):
                self._attach_file()

            if not self.file_path:
                logger.warning(
                    "File Excel '%s' belum ditemukan di OneDrive.",
                    self.filename,
                )
                return

            # Tunggu file selesai ditulis Excel atau OneDrive.
            previous_signature = None
            stable_checks = 0

            for _ in range(30):
                if self.stopped:
                    return

                if not os.path.isfile(self.file_path):
                    self._detach_file()
                    self._attach_file()

                    if not self.file_path:
                        time.sleep(1)
                        continue

                try:
                    stat = os.stat(self.file_path)
                    signature = (stat.st_size, stat.st_mtime_ns)
                except OSError:
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
                    "File Scorecard belum stabil; sinkronisasi dilewati."
                )
                return

            with open(self.file_path, "rb") as excel_file:
                content = excel_file.read()

            # Pastikan file tidak berubah saat sedang dibaca.
            stat_after = os.stat(self.file_path)

            if (
                stat_after.st_size != stat.st_size
                or stat_after.st_mtime_ns != stat.st_mtime_ns
            ):
                logger.info(
                    "File berubah saat dibaca. Sinkronisasi akan dijadwalkan ulang."
                )
                self.schedule_sync()
                return

            result = import_scorecard_excel(
                file_bytes=content,
                sales_office=self.sales_office,
                report_period=self.report_period,
                source_file=os.path.basename(self.file_path),
            )

            logger.info("Scorecard tersinkron: %s", result)

        except Exception:
            logger.exception(
                "Sinkronisasi Scorecard gagal. Periksa log backend "
                "dan koneksi Supabase development."
            )
        finally:
            self.lock.release()

    def _attach_file(self):
        folders = find_onedrive_folders()

        if not folders:
            logger.warning(
                "Folder OneDrive tidak terdeteksi. "
                "Pastikan OneDrive sudah terpasang dan tersinkron."
            )
            return

        found_path = find_excel_file(folders, self.filename)

        if not found_path:
            return

        if found_path == self.file_path and self.observer is not None:
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

                if hasattr(event, "dest_path"):
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
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None

        self.file_path = None

    def schedule_sync(self):
        if self.stopped:
            return

        with self.state_lock:
            if self.timer is not None:
                self.timer.cancel()

            self.timer = threading.Timer(2, self.sync_file)
            self.timer.daemon = True
            self.timer.start()

    def _search_again(self):
        if self.stopped:
            return

        try:
            # Cari ulang secara berkala jika file belum ditemukan
            # atau lokasinya berubah.
            if not self.file_path or not os.path.isfile(self.file_path):
                self._detach_file()
                self._attach_file()

                if self.file_path:
                    self.schedule_sync()
        except Exception:
            logger.exception("Pencarian ulang file Scorecard gagal.")
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
            # Impor awal jika file sudah ditemukan.
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
