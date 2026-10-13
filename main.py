from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import salesmen, stores, lks

app = FastAPI(title="SPA Performance API")

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://itshardtofindname.github.io", # URL GitHub Pages Frontend
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Bebaskan saat dev, batasi ke origins saat prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(salesmen.router)
app.include_router(stores.router)
app.include_router(lks.router)

@app.get("/")
def root():
    return {"status": "SPA Backend Active"}