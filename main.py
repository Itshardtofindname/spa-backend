from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from routers import lks, salesmen, stores

app = FastAPI(
    title="SPA Dashboard API",
    description="Backend API untuk Salesforce Performance Assistance System",
    version="1.0.0",
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