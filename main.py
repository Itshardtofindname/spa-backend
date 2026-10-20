
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from routers import lks, salesmen, stores, selling_toolkit, scorecard
from services.scorecard_watcher import ScorecardExcelWatcher

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

scorecard_watcher = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global scorecard_watcher

    auto_sync_enabled = (
        os.getenv("SCORECARD_AUTO_SYNC", "false").strip().lower()
        == "true"
    )

    if auto_sync_enabled:
        filename = os.getenv(
            "SCORECARD_FILE_PATTERN",
            "RAPOT Produktifitas S003 - September 2026.xlsx",
        ).strip()

        sales_office = os.getenv(
            "SCORECARD_SALES_OFFICE",
            "S003 - SO SERPONG",
        ).strip()

        report_period = os.getenv(
            "SCORECARD_REPORT_PERIOD",
            "2026-09-01",
        ).strip()

        try:
            if not filename:
                raise ValueError(
                    "SCORECARD_FILE_PATTERN belum diatur di .env"
                )

            if not sales_office:
                raise ValueError(
                    "SCORECARD_SALES_OFFICE belum diatur di .env"
                )

            if not report_period:
                raise ValueError(
                    "SCORECARD_REPORT_PERIOD belum diatur di .env"
                )

            scorecard_watcher = ScorecardExcelWatcher(
                filename=filename,
                sales_office=sales_office,
                report_period=report_period,
            )

            scorecard_watcher.start()

            logger.info(
                "Auto-sync Scorecard aktif untuk development."
            )

        except Exception:
            logger.exception(
                "Watcher Scorecard gagal diaktifkan."
            )

            if scorecard_watcher is not None:
                try:
                    scorecard_watcher.stop()
                except Exception:
                    logger.exception(
                        "Gagal menghentikan watcher Scorecard."
                    )

            scorecard_watcher = None

    else:
        logger.info(
            "Auto-sync Scorecard tidak aktif."
        )

    try:
        yield

    finally:
        if scorecard_watcher is not None:
            try:
                scorecard_watcher.stop()
            except Exception:
                logger.exception(
                    "Gagal menghentikan watcher Scorecard."
                )
            finally:
                scorecard_watcher = None


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
    allow_methods=["*"],
    allow_headers=["*"],
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
