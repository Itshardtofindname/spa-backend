from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import salesmen, stores, lks

app = FastAPI(
    title="SPA Dashboard API",
    description="Backend API untuk Salesforce Performance Assistance System (Supabase + FastAPI)",
    version="1.0.0"
)

# Konfigurasi CORS agar React/Vite Frontend bisa mengakses API ini
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Ganti dengan URL Frontend kamu saat produksi
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Routers
app.include_router(salesmen.router)
app.include_router(stores.router)
app.include_router(lks.router)

@app.get("/")
def root():
    return {"message": "API SPA Dashboard Aktif dan Terhubung!"}