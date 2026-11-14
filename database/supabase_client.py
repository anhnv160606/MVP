from config.settings import Settings
from supabase import Client, create_client

def get_supabase_client(settings: Settings) -> Client:
    if not settings.supabase_configured:
        raise ValueError("Supabase chưa được cấu hình. Vui lòng kiểm tra biến môi trường.")
    return create_client(str(settings.supabase_url), settings.supabase_key.get_secret_value())
    
