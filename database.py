import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Wajib ditambahkan agar variabel di file .env terbaca saat dijalankan di lokal
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# Inisialisasi aman tanpa melempar exception crash
if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None