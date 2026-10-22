
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from routers import lks, salesmen, stores, selling_toolkit, scorecard
from spa_scorecard_sync import (
    auto_sync_enabled,
    read_config,
    sync_from_drive,
    log_sync_result,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


async def scorecard_sync_loop(config: dict) -> None:
    """Periksa dan sinkronkan Scorecard secara berkala."""

    logger.info(
        "Central Sync Agent dimulai | office=%s | period=%s | interval=%ss",
        config["sales_office"],
        config["report_period"],
        config["poll_interval"],
    )

    try:
        while True:
            try:
                logger.info(
                    "Meminta backend memeriksa perubahan Google Drive."
                )

                # Fungsi sinkronisasi menggunakan HTTP blocking.
                # Jalankan di thread agar event loop FastAPI tetap responsif.
                result = await asyncio.to_thread(
                    sync_from_drive,
                    config,
                )

                log_sync_result(result)

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception(
                    "Pemeriksaan sinkronisasi Scorecard gagal. "
                    "Akan dicoba kembali pada interval berikutnya."
                )

            await asyncio.sleep(config["poll_interval"])

    finally:
        logger.info("Central Sync Agent dihentikan.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SPA Backend API starting.")

    sync_task = None

    try:
        try:
            if auto_sync_enabled():
                config = read_config()
                sync_task = asyncio.create_task(
                    scorecard_sync_loop(config),
                    name="spa-scorecard-sync",
                )
                app.state.scorecard_sync_task = sync_task
            else:
                logger.info(
                    "Central Sync Agent dinonaktifkan. "
                    "SCORECARD_AUTO_SYNC=false."
                )

        except Exception:
            # Kesalahan konfigurasi agent tidak menghentikan API.
            logger.exception(
                "Central Sync Agent tidak dapat dimulai. "
                "Backend tetap berjalan tanpa sinkronisasi otomatis."
            )

        yield

    finally:
        if sync_task is not None:
            sync_task.cancel()

            try:
                await sync_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "Terjadi kesalahan saat menghentikan Sync Agent."
                )

        logger.info("SPA Backend API shutting down.")


app = FastAPI(
    title="SPA Dashboard API",
    description="Backend API untuk Salesforce Performance Assistance System",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-Import-Token",
        "Authorization",
    ],
)


@app.get("/", tags=["Health"])
def root():
    return {"status": "SPA Backend Active"}


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}


app.include_router(salesmen.router)
app.include_router(stores.router)
app.include_router(lks.router)
app.include_router(selling_toolkit.router)
app.include_router(scorecard.router)
