from supabase import Client, create_client

from config import SUPABASE_KEY, SUPABASE_URL

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_URL dan SUPABASE_KEY wajib diatur."
    )

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)