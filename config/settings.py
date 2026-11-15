from typing import Literal #Chỉ nhận các giá trị mặc định
from pydantic import HttpUrl, SecretStr #check đường dẫn web| Đảm bảo không in ra mật khẩu
from pydantic_settings import BaseSettings, SettingsConfigDict #load cấu hình từ biến môi trường và file .env


class Settings(BaseSettings):
    # tự động tìm kiếm giá trị khớp với tên thuộc tính 

    model_config = SettingsConfigDict(
        env_file=".env", #tên file chứa biến môi trường
        env_file_encoding="utf-8", #mã hóa file .env
        env_ignore_empty=True, #bỏ qua các biến môi trường rỗng
        extra="ignore", #bỏ qua các biến môi trường không khớp với tên thuộc tính
        hide_input_in_errors=True, #ẩn giá trị của thuộc tính trong thông báo lỗi
        case_sensitive=False, #không phân biệt chữ hoa chữ thường
    )

    app_env: Literal["dev", "test", "prod"] = "dev" #giá trị mặc định là dev
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info" #giá trị mặc định là info
    supabase_url: HttpUrl | None = None #kiểm tra đường dẫn web hợp lệ
    supabase_key: SecretStr | None = None #đảm bảo không in ra mật khẩu

    @property 
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

#test class Settings
if __name__ == "__main__":
    settings = Settings()
    print(settings.supabase_configured)
