
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from database import supabase

router = APIRouter(
    prefix="/api/selling-toolkit",
    tags=["Selling Toolkit"],
)


def first_or_none(rows):
    return rows[0] if rows else None


@router.get("/{store_id}/lks-periods")
def get_lks_periods(store_id: str):
    """Mengambil daftar periode LKS yang tersedia untuk toko."""
    try:
        response = (
            supabase.table("lks_product_targets")
            .select("periode")
            .eq("store_id", store_id)
            .execute()
        )

        periods = sorted(
            {
                row["periode"]
                for row in (response.data or [])
                if row.get("periode")
            }
        )

        return {"storeId": store_id, "periods": periods}

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Gagal mengambil daftar periode LKS.",
        ) from exc


@router.get("/{store_id}")
def get_selling_toolkit(
    store_id: str,
    period: Optional[str] = Query(
        default=None,
        description="Periode LKS yang dipilih frontend, misalnya Sep-26.",
    ),
):
    """
    store_id adalah kode pelanggan, misalnya 1000002996.
    Ini bukan UUID dari tabel stores.id.
    """
    try:
        # 1. Profil Selling Toolkit berdasarkan kode pelanggan.
        profile_response = (
            supabase.table("store_profiles")
            .select("*")
            .eq("store_id", store_id)
            .limit(1)
            .execute()
        )
        profile = first_or_none(profile_response.data or [])

        # 2. Data operasional toko. cust_no berisi kode pelanggan,
        # sedangkan stores.id adalah UUID untuk relasi order dan kunjungan.
        store_response = (
            supabase.table("stores")
            .select("*")
            .eq("cust_no", store_id)
            .limit(1)
            .execute()
        )
        store = first_or_none(store_response.data or [])

        if not profile and not store:
            raise HTTPException(
                status_code=404,
                detail=f"Toko dengan kode {store_id} tidak ditemukan.",
            )

        # 3. Informasi salesman, jika profil menyimpan NPK.
        npk_salesman = (profile or {}).get("npk_salesman")
        salesman = None

        if npk_salesman:
            # Mendukung format "4831 - Seno Aji Sobirin"
            npk = str(npk_salesman).split(" - ", 1)[0].strip()

            salesman_response = (
                supabase.table("salesmen")
                .select("id,npk,nama,sales_office,grup_product")
                .eq("npk", npk)
                .limit(1)
                .execute()
            )
            salesman = first_or_none(salesman_response.data or [])

        # 4. Program dan rekomendasi produk.
        programs_response = (
            supabase.table("store_programs")
            .select("*")
            .eq("store_id", store_id)
            .order("id")
            .execute()
        )

        cross_sell_response = (
            supabase.table("store_cross_sell_items")
            .select("*")
            .eq("store_id", store_id)
            .order("id")
            .execute()
        )

        # 5. Target LKS mengikuti periode yang dipilih di frontend.
        # Jika period tidak dikirim, lksTargets akan menjadi [].
        lks_targets = []

        if period and period.strip():
            lks_response = (
                supabase.table("lks_product_targets")
                .select("*")
                .eq("store_id", store_id)
                .eq("periode", period.strip())
                .order("level_type")
                .order("code_sku")
                .execute()
            )
            lks_targets = lks_response.data or []

        # 6. Kunjungan dan order menggunakan UUID stores.id.
        store_uuid = (store or {}).get("id")
        visits = []
        recent_orders = []

        if store_uuid:
            visits_response = (
                supabase.table("store_visits")
                .select("*")
                .eq("store_id", store_uuid)
                .order("tanggal", desc=True)
                .limit(10)
                .execute()
            )
            visits = visits_response.data or []

            orders_response = (
                supabase.table("orders")
                .select("*")
                .eq("store_id", store_uuid)
                .order("tanggal_order", desc=True)
                .limit(10)
                .execute()
            )
            recent_orders = orders_response.data or []

        # 7. Gabungkan ke bentuk response frontend.
        data = profile or {}
        operational_store = store or {}

        return {
            "storeId": store_id,
            "storeName": (
                data.get("store_name")
                or operational_store.get("nama_toko")
                or ""
            ),
            "owner": data.get("owner"),
            "npkSalesman": npk_salesman,
            "salesman": salesman,
            "visitSchedule": data.get("visit_schedule"),
            "visitStatus": data.get("visit_status"),
            "creditLimit": {
                "status": data.get("credit_status"),
                "usedAmount": data.get("used_amount") or 0,
                "usedPercentage": data.get("used_percentage"),
                "totalLimit": (
                    data.get("total_limit")
                    if data.get("total_limit") is not None
                    else operational_store.get("plafon_total", 0)
                ),
                "remainingCredit": (
                    data.get("remaining_credit")
                    if data.get("remaining_credit") is not None
                    else operational_store.get("sisa_limit", 0)
                ),
                "percentage": data.get("credit_percentage_label"),
                "manualBlock": data.get("manual_block"),
                "overdueInvoices": data.get("overdue_invoices"),
            },
            "programs": programs_response.data or [],
            "crossSellItems": cross_sell_response.data or [],
            "lksPeriod": period.strip() if period and period.strip() else None,
            "lksTargets": lks_targets,
            "visits": visits,
            "recentOrders": recent_orders,
        }

    except HTTPException:
        raise
    except Exception as exc:
        # Jangan mengirim detail internal database ke client.
        raise HTTPException(
            status_code=500,
            detail="Gagal mengambil data Selling Toolkit dari database.",
        ) from exc
