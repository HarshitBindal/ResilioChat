import os
from typing import List, Tuple
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()


class Settings:
    @property
    def LLM_API_KEYS_RAW(self) -> str:
        return os.getenv("LLM_API_KEYS", "")

    @property
    def LLM_API_KEYS(self) -> List[Tuple[str, str]]:
        """
        Parses comma-separated keys and maps them in order to:
        [('key_1', raw_key_1), ('key_2', raw_key_2), ...]
        """
        raw = self.LLM_API_KEYS_RAW.strip()
        if not raw:
            return []
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        return [(f"key_{i+1}", key) for i, key in enumerate(keys)]

    @property
    def LLM_MODEL(self) -> str:
        return os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash")

    @property
    def DATABASE_URL(self) -> str:
        return os.getenv("DATABASE_URL", "sqlite:///./data/app.db")

    @property
    def ALLOWED_ORIGINS(self) -> List[str]:
        raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def ENABLE_FAILURE_SIMULATION(self) -> bool:
        val = os.getenv("ENABLE_FAILURE_SIMULATION", "true").strip().lower()
        return val in ("true", "1", "yes", "on")


settings = Settings()
