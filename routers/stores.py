from fastapi import APIRouter, HTTPException
from database import supabase
from schemas import StoreResponse
from typing import List

router = APIRouter(prefix="/api/stores", tags=["Stores / Toko"])

@router.get("/", response_model=List[StoreResponse])
def get_stores_by_salesman(salesman_id: str = None):
    query = supabase.table("stores").select("*")
    if salesman_id:
        query = query.eq("salesman_id", salesman_id)
    response = query.execute()
    return response.data

@router.get("/{store_id}")
def get_store_detail(store_id: str):
    response = supabase.table("stores").select("*").eq("id", store_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Toko tidak ditemukan")
    return response.data[0]