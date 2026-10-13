from pydantic import BaseModel
from typing import Optional, List
from datetime import date, datetime

class SalesmanBase(BaseModel):
    npk: str
    nama: str
    sales_office: str
    grup_product: str

class SalesmanResponse(SalesmanBase):
    id: str
    created_at: Optional[datetime] = None

class StoreBase(BaseModel):
    cust_no: str
    nama_toko: str
    area: str
    status_akun: str
    plafon_total: float
    sisa_limit: float

class StoreResponse(StoreBase):
    id: str
    salesman_id: Optional[str] = None