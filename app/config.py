"""Application settings, loaded from environment variables (or a .env file)."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Account fileupload fields that may hold customer documents (api names from Zoho field metadata).
DEFAULT_FILE_FIELDS = [
    "BIR_Registration_COR",
    "Business_registration",
    "Business_Permit",
    "General_Information_Sheet",
]


# Account fields saved in each job's snapshot. Later phases compare documents against these, and
# the write-back step uses them to detect CRM edits made during review.
DEFAULT_SNAPSHOT_FIELDS = [
    "id", "Account_Name", "Invoice_Company_Name", "Inventory_Customer_Number",
    "Customer_Status", "Lead_Category", "Credit_Terms", "Owner",
    "Owner_Name", "Owner_Position", "Owner_Address",
    "Type_of_Business_Organization", "Business_Style",
    "Tax_Identification_Number_TIN", "Corp_Tax_Cert_No",
    "Billing_Street", "Billing_Barangay", "Billing_City", "Billing_City1",
    "Billing_Province", "Billing_State", "Billing_Code", "Billing_Country",
    "Shipping_Street", "Shipping_Barangay", "Shipping_City", "Shipping_Province",
    "Shipping_State", "Shipping_Code",
    "Email", "Phone", "Phone_Number", "Contact_Number",
    "Delivery_Permit", "Special_Permit",
    "BIR_Registration_COR", "Business_registration", "Business_Permit",
    "General_Information_Sheet",
    "Created_Time", "Modified_Time", "Modified_By",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    log_level: str = "INFO"
    api_key: SecretStr

    database_url: str = "postgresql+psycopg://dv:dv@localhost:5432/dv"
    redis_url: str = "redis://localhost:6379/0"

    zoho_accounts_url: str = "https://accounts.zoho.com"
    zoho_api_domain: str = "https://www.zohoapis.com"
    zoho_api_version: str = "v8"
    zoho_client_id: str
    zoho_client_secret: SecretStr
    zoho_refresh_token: SecretStr
    zoho_max_retries: int = 5
    zoho_timeout_seconds: float = 60.0
    zoho_file_fields: list[str] = DEFAULT_FILE_FIELDS
    zoho_snapshot_fields: list[str] = DEFAULT_SNAPSHOT_FIELDS

    storage_backend: str = "local"  # "local" | "s3"
    storage_local_dir: str = "/data/files"
    s3_endpoint_url: str | None = None
    s3_region: str = "ap-southeast-1"
    s3_bucket: str = "dv-documents"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    max_file_bytes: int = 25 * 1024 * 1024

    # Phase 1C: vision-model extraction. "none" stops jobs after reading.
    extraction_provider: str = "none"          # anthropic | openai | none
    extraction_model: str | None = None        # default for anthropic: claude-sonnet-5-5
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    extraction_reasoning_effort: str = "low"   # OpenAI only: none | low | medium | high

    sentry_dsn: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
