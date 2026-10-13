import os
import pandas as pd
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

FILE_EXCEL = r"C:\Users\Giovan\Downloads\RAPOT Produktifitas S003 - September 2026.xlsx"

def import_lks_data():
    print(f"Membaca data dari {FILE_EXCEL}...")
    
    # Baca sheet Excel
    df_lks = pd.read_excel(FILE_EXCEL, sheet_name=0) 
    
    # Bersihkan NaN
    df_lks = df_lks.fillna({
        "avg_2025": 0,
        "avg_2026": 0,
        "target_qty": 0,
        "realisasi_qty": 0,
        "metode_rekomendasi": "UP-SELLING"
    })

    records = df_lks.to_dict(orient="records")

    for row in records:
        payload = {
            "store_id": str(row.get("store_id", "1000015851")), # Disimpan sebagai string cust_no
            "periode": "Sep-26",
            "level_type": str(row.get("level_type", "L2")),
            "code_sku": str(row.get("code_sku", "ATI")),
            "nama_produk": str(row.get("nama_produk", "Astra Tire Tube")),
            "avg_2025": int(row.get("avg_2025", 0)),
            "avg_2026": int(row.get("avg_2026", 0)),
            "target_qty": int(row.get("target_qty", 0)),
            "realisasi_qty": int(row.get("realisasi_qty", 0)),
            "metode_rekomendasi": str(row.get("metode_rekomendasi", "UP-SELLING"))
        }
        
        # Kirim ke Supabase
        supabase.table("lks_product_targets").upsert(payload).execute()

    print(f"✅ Berhasil menyinkronkan {len(records)} baris data ke Supabase!")

if __name__ == "__main__":
    import_lks_data()