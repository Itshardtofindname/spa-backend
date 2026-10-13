from fastapi import APIRouter, Request, HTTPException
import pandas as pd
import io
from database import supabase

# Prefix ini harus /api/lks
router = APIRouter(prefix="/api/lks", tags=["LKS Sync"])

@router.post("/upload-rapot")
async def upload_full_rapot(request: Request):
    try:
        contents = await request.body()
        if not contents:
            raise HTTPException(status_code=400, detail="File Excel kosong")

        excel_file = pd.ExcelFile(io.BytesIO(contents))
        sheet_names = excel_file.sheet_names

        # ... (Logika pemrosesan sheet dan upsert ke Supabase)

        return {
            "status": "success",
            "message": "Seluruh data Excel berhasil disinkronkan ke Supabase!",
            "sheets": sheet_names
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))