from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from routers import salesmen, stores, lks

app = FastAPI(title="SPA Backend API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Endpoint khusus menangani favicon request dari browser
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(content=b"", media_type="image/x-icon")

app.include_router(salesmen.router)
app.include_router(stores.router)
app.include_router(lks.router)

@app.get("/")
def root():
    return {"status": "SPA Backend Active"}