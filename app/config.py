import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./qd_local.db")
PAYSWIFT_BASE_URL = os.getenv("PAYSWIFT_BASE_URL", "http://localhost:8081").rstrip("/")
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))

# Optional LLM (Groq or Gemini free tier). With no key the agent runs fully deterministic.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").lower()          # "groq" | "gemini" | ""
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "4"))

# PaySwift call budget inside one /messages request (vendor retries after ~10s).
PAYSWIFT_BUDGET_S = float(os.getenv("PAYSWIFT_BUDGET_S", "6.0"))
PAYSWIFT_TRY_TIMEOUT_S = float(os.getenv("PAYSWIFT_TRY_TIMEOUT_S", "2.5"))
RECONCILE_INTERVAL_S = float(os.getenv("RECONCILE_INTERVAL_S", "4"))

ALLOW_ADMIN_RESET = os.getenv("ALLOW_ADMIN_RESET", "0") == "1"

AUTO_PAY_LIMIT_CFG = int(os.getenv("AUTO_PAY_LIMIT", "200"))
