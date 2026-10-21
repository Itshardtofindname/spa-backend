
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from routers import lks, salesmen, stores, selling_toolkit, scorecard


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SPA Backend API starting.")

    try:
        yield
    finally:
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
