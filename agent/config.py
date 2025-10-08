import os
import warnings
from typing import Dict, Any
from dotenv import load_dotenv
from redis import Redis
from redis.connection import ConnectionPool

# Load environment variables from .env file if it exists
load_dotenv()

# Database Configuration
DATABASE_CONFIG = {
    "username": os.getenv("MONGODB_USERNAME", "alfreddeveloper_db_user"),
    "password": os.getenv("MONGODB_PASSWORD", "sBqjBA-n.5NX-qb"),
    "cluster": os.getenv("MONGODB_CLUSTER", "alfreddemo.dcqqgb8.mongodb.net"),
    "database": os.getenv("MONGODB_DATABASE", "alfreddemo"),
    "communications_db": os.getenv("COMMUNICATIONS_DB", "alfred_communications"),
    "risk_db": os.getenv("RISK_DB", "alfred_risks"),
    "task_db": os.getenv("TASK_DB", "alfred_tasks") ,
    "sites_db": os.getenv("SITES_DB", "alfred_sites"),
    "package_db": os.getenv("PACKAGE_DB", "alfred_cwp"),
    "notifications_db": os.getenv("NOTIFICATIONS_DB", "alfred_notifications"),
    "assets_db": os.getenv("ASSETS_DB", "alfred_assets"),
    "cwp_db": os.getenv("CWP_DB", "alfred_cwp"),
    "iwp_db": os.getenv("IWP_DB", "alfred_iwp"),
    "project_id": os.getenv("PROJECT_ID"),
    "email_event_log": os.getenv("EMAIL_EVENT_LOG", "alfred_email_event_log"),
}

def get_database_uri() -> str:
    """Construct MongoDB Atlas connection URI."""
    return (
        f"mongodb+srv://{DATABASE_CONFIG['username']}:{DATABASE_CONFIG['password']}"
        f"@{DATABASE_CONFIG['cluster']}/{DATABASE_CONFIG['database']}?retryWrites=true&w=majority&appName=alfreddemo"
    )

# Qdrant Configuration
QDRANT_CONFIG = {
    "url": os.getenv("QDRANT_URL", "https://65b72759-7e05-43e9-a142-f666ee946cf1.us-east4-0.gcp.cloud.qdrant.io:6333"),
    "api_key": os.getenv("QDRANT_API_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIn0.EFenEXFVOI4eEzxDOko_Teu6L1MBr9sEcTqBLq3Hgro"),
    "collection": os.getenv("QDRANT_COLLECTION", "communications_gmail"),
    "vector_dim": int(os.getenv("VECTOR_DIM", 1536)),
}

def _resolve_google_api_key() -> str | None:
    """Resolve the Google/Gemini API key from multiple possible environment variables."""
    for var in [
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_GENAI_API_KEY",
        "GOOGLE_GEMINI_API_KEY",
        "GENAI_API_KEY",
    ]:
        val = os.getenv(var)
        if val and val.strip():
            return val.strip()
    return None

# Google Generative AI Configuration
GOOGLE_CONFIG={
    "api_key": _resolve_google_api_key(),
    "model": os.getenv("LLM_MODEL_NAME", "gemini-2.0-flash-lite"),
}

# Application Settings
# Default log file under the package's logs directory so it is stable across CWDs
_PACKAGE_DIR = os.path.dirname(__file__)
_DEFAULT_LOG_PATH = os.path.join(_PACKAGE_DIR, "logs", "app.log")
APP_CONFIG = {
    "max_workers": int(os.getenv("MAX_WORKERS", 4)),
    "log_file": os.getenv("LOG_FILE", _DEFAULT_LOG_PATH),
}

GCP_STORAGE_CONFIG = {
    "GCS_BUCKET_NAME": os.getenv("GCS_BUCKET_NAME", "pathsetter-alfred-storage"),
    "GOOGLE_APPLICATION_CREDENTIALS": os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    "ENABLE_ATTACHMENT_PROCESSING": os.getenv("ENABLE_ATTACHMENT_PROCESSING", "true").lower() == "true"
}

# Gmail API Configuration
GMAIL_CONFIG = {
    "scopes": [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.readonly"
    ],
    "max_results": int(os.getenv("GMAIL_MAX_RESULTS", 100)),
    # Centralized Gmail credential paths
    "creds_path": os.getenv("CREDS_PATH"),
    "token_path": os.getenv("TOKEN_PATH"),
}

# Global variable to store current project_id from trigger request
_current_project_id = None

def set_current_project_id(project_id: str):
    """Set the current project_id for this processing session"""
    global _current_project_id
    _current_project_id = project_id

def get_current_project_id() -> str:
    """Get the current project_id for this processing session"""
    global _current_project_id
    return _current_project_id or os.getenv("PROJECT_ID")

def get_project_id() -> str:
    """Alias for get_current_project_id for backward compatibility"""
    return get_current_project_id()

SCHEDULER_CONFIG = {
    "project_id": os.getenv("PROJECT_ID"),
    'interval_minutes': int(os.getenv("RUN_INTERVAL_MINUTES", "3")), 
    'batch_size': 5,
    'max_retries': 3,
    'retry_delay': 2,  # Base retry delay in seconds
    'inter_batch_delay': 30,
    'enable_scheduling': True,
}

redis_pool = ConnectionPool(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
    username=os.getenv("REDIS_USERNAME"),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
    max_connections=10,  # should be below Redis Cloud plan limit
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True
)


def get_redis_connection() -> Redis:
    """Get a Redis connection from the pool."""
    return Redis(connection_pool=redis_pool)

def close_redis_connections():
    """Close all connections in the Redis pool."""
    redis_pool.disconnect()
# email_agent/org_config.py

# Dictionary mapping organization IDs to their email configurations
ORG_EMAIL_CONFIG = {
    # Organization 1
    "org_123": {
        "email": "alfredpurelight@gmail.com",
        "credentials_path": "creds.json",
        "token_path": "token.json"
    },
    # Organization 2
    "org_456": {
        "email": "alfredpurelight@gmail.com",
        "credentials_path": "creds.json",
        "token_path": "token.json"
    },
    # Add more organizations as needed
    "default": {
        "email": "alfredpurelight@gmail.com",
        "credentials_path": "creds.json",
        "token_path": "token.json"
    }
}

def get_org_config(org_id):
    """Get email configuration for a specific organization."""
    return ORG_EMAIL_CONFIG.get(org_id, ORG_EMAIL_CONFIG["default"])

def validate_config() -> None:
    """
    Validate that all required configuration is present.
    Only shows warnings for production environment or if explicitly requested.
    """
    environment = os.getenv("ENVIRONMENT", "development").lower()
    show_warnings = environment == "production" or os.getenv("SHOW_CONFIG_WARNINGS", "false").lower() == "true"
    
    if not show_warnings:
        return
    
    # Checking for missing critical environment variables
    critical_vars = {
        "GOOGLE_API_KEY": "Required for Google Generative AI access (Gemini)",
    }
    
    # Checking for production-specific variables
    production_vars = {
        "MONGODB_PASSWORD": "Using default password (not recommended for production)",
    }
    
    missing_critical = []
    missing_production = []
    
    for var, msg in critical_vars.items():
        if not os.getenv(var) and not GOOGLE_CONFIG.get("api_key"):
            missing_critical.append(var)
    
    for var, msg in production_vars.items():
        if not os.getenv(var) and environment == "production":
            missing_production.append(var)
    
    # Showing warnings
    if missing_critical:
        warnings.warn(
            f"Critical environment variables are not set: {', '.join(missing_critical)}. "
            f"The application may not function correctly."
        )
    
    if missing_production and environment == "production":
        warnings.warn(
            f"Production environment variables are not set: {', '.join(missing_production)}. "
            f"Using default values which are not suitable for production."
        )

# Validating configuration on import
validate_config()

# Exporting configurations 
__all__ = [
    'DATABASE_CONFIG',
    'QDRANT_CONFIG', 
    'GOOGLE_CONFIG',
    'APP_CONFIG',
    'GMAIL_CONFIG',
    'SCHEDULER_CONFIG',
    'get_database_uri',
    'validate_config',
    'GCP_STORAGE_CONFIG',
    'get_redis_connection',
    'set_current_project_id',
    'get_current_project_id',
    'get_project_id',
]

