from dataclasses import dataclass, field
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", encoding="utf-8-sig")


@dataclass
class Settings:
    data_dir: Path = Path(os.getenv("TRACELENS_DATA_DIR", str(ROOT / "data"))).resolve()
    base_url: str = os.getenv("OPENAI_BASE_URL", "").rstrip("/")
    api_key: str = os.getenv("OPENAI_API_KEY", "")
    model: str = os.getenv("DIAG_MODEL", "")
    prompt_language: str = os.getenv("DIAG_PROMPT_LANGUAGE", "en")
    headers: dict[str, str] = field(default_factory=dict)
    request_parameters: dict = field(default_factory=dict)
    concurrency: int = max(1, min(8, int(os.getenv("DIAG_CONCURRENCY", "2"))))
    max_retries: int = max(0, min(5, int(os.getenv("DIAG_MAX_RETRIES", "2"))))
    timeout: float = float(os.getenv("DIAG_TIMEOUT", "120"))
    llm_concurrency: int = max(1, min(16, int(os.getenv("DIAG_LLM_CONCURRENCY", "8"))))
    max_tokens: int = max(1024, int(os.getenv("DIAG_MAX_TOKENS", "32768")))
    cluster_json_mode: bool = os.getenv("DIAG_CLUSTER_JSON_MODE", "false").lower() == "true"
    enable_validation_data: bool = os.getenv("TRACELENS_ENABLE_VALIDATION_DATA", "false").lower() == "true"

    @property
    def ready(self):
        return bool(self.base_url and self.model)

