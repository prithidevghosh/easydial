from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: str = ""
    openai_text_model: str = "gpt-4.1-mini"
    openai_transcription_model: str = "gpt-4o-transcribe"
    openai_speech_model: str = "gpt-4o-mini-tts"
    openai_voice: str = "coral"
    commerce_mode: str = "live"
    browser_headless: bool = False
    browser_channel: str = "chrome"
    instamart_browser_profile: str = ""
    instamart_verified_location: str = ""
    instamart_verified_address: str = ""
    blinkit_browser_profile: str = ""
    live_address_label: str = ""
    blinkit_storage_state: str = ""
    blinkit_verified_location: str = ""
    blinkit_verified_address: str = ""
    # WhatsApp is a single-account pilot; exactly one allowed sender, never a wildcard.
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = ""
    meta_app_secret: str = ""
    whatsapp_allowed_sender: str = ""
    whatsapp_api_version: str = "v27.0"
    whatsapp_database: str = ".state/whatsapp/pilot.sqlite3"
    whatsapp_support_contact: str = ""
