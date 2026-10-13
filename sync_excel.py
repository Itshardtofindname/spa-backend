import os
import pandas as pd
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

EXCEL_PATH = "data_lks_sales.xlsx"

def sync_excel_to_supabase():
    print("Membaca file Excel...")
    
    # 1. Baca Sheet Target LKS dari Excel
    df_lks = pd.read_excel(EXCEL_PATH, sheet_name="Target_LKS")
    
    # Clean data / isi nilai null
    df_lks = df_lks.fillna({
        "avg_2025": 0,
        "avg_2026": 0,
        "target_qty": 0,
        "realisasi_qty": 0
    })

    # 2. Convert Dataframe ke bentuk Dictionary
    records = df_lks.to_dict(orient="records")

    # 3. Upsert (Insert/Update) ke Tabel Supabase `lks_product_targets`
    for row in records:
        data = {
            "store_id": str(row["store_id"]),
            "periode": str(row["periode"]),
            "level_type": str(row["level_type"]),
            "code_sku": str(row["code_sku"]),
            "nama_produk": str(row["nama_produk"]),
            "avg_2025": int(row["avg_2025"]),
            "avg_2026": int(row["avg_2026"]),
            "target_qty": int(row["target_qty"]),
            "realisasi_qty": int(row["realisasi_qty"]),
            "metode_rekomendasi": str(row.get("metode_rekomendasi", "UP-SELLING"))
        }
        
        # Lakukan upsert ke database
        supabase.table("lks_product_targets").upsert(data).execute()
        
    print(f"Berhasil menyinkronkan {len(records)} baris data dari Excel ke Supabase!")

if __name__ == "__main__":
    sync_excel_to_supabase()