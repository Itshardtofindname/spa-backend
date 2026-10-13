from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import router spesifik dari folder routers
from routers.salesmen import router as salesmen_router
from routers.stores import router as stores_router
from routers.lks import router as lks_router

app = FastAPI(
    title="SPA Dashboard API",
    description="Backend API untuk Salesforce Performance Assistance System",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Routers
app.include_router(salesmen_router)
app.include_router(stores_router)
app.include_router(lks_router)

@app.get("/")
def root():
    return {"message": "API SPA Dashboard Aktif dan Terhubung!"}