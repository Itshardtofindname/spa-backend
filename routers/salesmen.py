from fastapi import APIRouter, HTTPException, Query
from database import supabase
from typing import Optional

router = APIRouter(prefix="/api/salesmen", tags=["Salesmen Performance"])

@router.get("/options")
def get_filter_options():
    """Mengambil daftar dinamis Sales Office, Grup Product, dan Salesman dari Supabase"""
    res = supabase.table("salesmen").select("sales_office, grup_product, nama, npk").execute()
    
    if not res.data:
        return {"offices": [], "groups": [], "salesmen": []}

    offices = sorted(list(set([item["sales_office"] for item in res.data if item.get("sales_office")])))
    groups = sorted(list(set([item["grup_product"] for item in res.data if item.get("grup_product")])))
    salesmen = sorted(list(set([item["nama"] for item in res.data if item.get("nama")])))

    return {
        "offices": offices,
        "groups": groups,
        "salesmen": salesmen
    }

@router.get("/performance")
def get_salesman_performance(
    office: Optional[str] = Query(None),
    group: Optional[str] = Query(None),
    salesman: Optional[str] = Query(None)
):
    """Mengambil detail data performa salesman dari Supabase"""
    query = supabase.table("salesmen").select("*, targets(*)")

    if salesman:
        query = query.ilike("nama", f"%{salesman}%")
    if office:
        query = query.eq("sales_office", office)
    if group:
        query = query.eq("grup_product", group)

    res = query.execute()

    if not res.data:
        raise HTTPException(status_code=404, detail="Data performa salesman tidak ditemukan di database")

    sales_data = res.data[0]
    
    # Format struktur response agar sesuai dengan komponen UI Dashboard
    targets = sales_data.get("targets", [])
    target_info = targets[0] if targets else {}

    return {
        "id": sales_data.get("id"),
        "salesman": sales_data.get("nama", "-"),
        "npk": sales_data.get("npk", "-"),
        "salesOffice": sales_data.get("sales_office", "-"),
        "grupProduct": sales_data.get("grup_product", "-"),
        "totalOutlet": target_info.get("total_outlet", 30),
        "rankSo": target_info.get("rank_so", "#1 dari 18 Sales"),
        "avgAchievement": target_info.get("avg_achievement", "70.0%"),
        "finalGrade": target_info.get("grade", "B"),
        "skorFinal": float(target_info.get("skor_kpi", 12.0)),
        "statusLulus": "HIGH" if float(target_info.get("skor_kpi", 12)) >= 9 else "LOW",
        "pilar": {
            "result": float(target_info.get("pilar_result", 1.0)),
            "strategic": float(target_info.get("pilar_strategic", 8.0)),
            "process": float(target_info.get("pilar_process", 3.0))
        },
        "scorecard": sales_data.get("scorecard", []),
        "trackRecord": sales_data.get("trackRecord", []),
        "repeatOrderRendah": sales_data.get("repeatOrderRendah", []),
        "kekuatanSales": sales_data.get("kekuatanSales", []),
        "catatanSupervisor": sales_data.get("catatanSupervisor", "")
    }