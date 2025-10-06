import os
import warnings
from typing import Dict, Any
from dotenv import load_dotenv
from redis import Redis
from redis.connection import ConnectionPool, SSLConnection

# Load environment variables from .env file if it exists
from pathlib import Path

load_dotenv()

# Database Configuration
DATABASE_CONFIG = {
    "username": os.getenv("MONGODB_USERNAME", "alfreddeveloper_db_user"),
    "password": os.getenv("MONGODB_PASSWORD", "sBqjBA-n.5NX-qb"),
    "cluster": os.getenv("MONGODB_CLUSTER", "alfreddemo.dcqqgb8.mongodb.net"),
    "database": os.getenv("MONGODB_DATABASE", "alfreddemo"),
    "projects_db": os.getenv("PROJECTS_DB", "alfred_projects"),
    "users_db": os.getenv("USERS_DB", "alfred_users"),
    "communications_db": os.getenv("COMMUNICATIONS_DB", "alfred_communications"),
    "risk_db": os.getenv("RISK_DB", "alfred_risks"),
    "task_db": os.getenv("TASK_DB", "alfred_tasks"),
    "sites_db": os.getenv("SITES_DB", "alfred_sites"),
    "package_db": os.getenv("PACKAGE_DB", "alfred_packages"),
    "notifications_db": os.getenv("NOTIFICATIONS_DB", "alfred_notifications"),
    "assets_db": os.getenv("ASSETS_DB", "alfred_assets"),
    "cwp_db": os.getenv("CWP_DB", "alfred_cwp"),
    "iwp_db": os.getenv("IWP_DB", "alfred_iwp"),
    "staging_db": os.getenv("STAGING_DB", "alfred_staging"),
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

# OpenAI/Azure Configuration
OPENAI_CONFIG = {
    "api_key": os.getenv("OPENAI_API_KEY", "your-default-api-key"),  # Added default
    "api_version": os.getenv("OPENAI_API_VERSION", "2024-12-01-preview"),  # Added default
    "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", "https://alfredapi.openai.azure.com"),  # Added default
    "azure_deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),  # Added default
    "embedding_deployment": os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-small"),
    "embedding_api_version": os.getenv("EMBEDDING_API_VERSION", "2023-05-15"),
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

PROJECT_CONFIG = {
    "project_id": os.getenv("PROJECT_ID"),
}

def get_project_id() -> str:
    """Return the canonical project_id from environment/config.
    Falls back across config blocks to keep a single source of truth.
    """
    return (
        PROJECT_CONFIG.get("project_id")
        or DATABASE_CONFIG.get("project_id")
        or os.getenv("PROJECT_ID")
    )
SCHEDULER_CONFIG = {
    "project_id": os.getenv("PROJECT_ID"),
    'interval_minutes': int(os.getenv("RUN_INTERVAL_MINUTES", "3")), 
    'batch_size': 5,
    'max_retries': 3,
    'retry_delay': 2,  # Base retry delay in seconds
    'inter_batch_delay': 30,
    'enable_scheduling': True,
}

# Redis configuration (optional)
# Toggle to enable/disable Redis usage. Defaults to disabled for local dev.
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "True").lower() == "true"
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_USERNAME = os.getenv("REDIS_USERNAME")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_SSL = os.getenv("REDIS_SSL", "false").lower() == "true"

redis_pool = None
redis_client = None
if REDIS_ENABLED:
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Redis config -> ENABLED={REDIS_ENABLED}, HOST={REDIS_HOST}, PORT={REDIS_PORT}, SSL={REDIS_SSL}")
    
    try:
        from redis import from_url
        
        if REDIS_SSL:
            redis_url = f"rediss://{REDIS_USERNAME}:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"
            redis_client = from_url(
                redis_url,
                decode_responses=True,
                ssl_cert_reqs=None,
                socket_connect_timeout=5,
                socket_timeout=5
            )
        else:
            redis_url = f"redis://{REDIS_USERNAME}:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"
            redis_client = from_url(redis_url, decode_responses=True)
        
        redis_client.ping()
        logger.info("✅ Redis connection successful")
        
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
        redis_client = None

def get_redis_connection() -> Redis:
    """Get a Redis connection from the pool.
    Raises a helpful error if Redis is disabled.
    """
    if not REDIS_ENABLED or (redis_pool is None and redis_client is None):
        raise RuntimeError(
            "Redis is disabled. Set REDIS_ENABLED=true and configure REDIS_HOST/REDIS_PORT to enable it."
        )
    # Prefer the direct client if available
    if redis_client is not None:
        return redis_client
    # Otherwise, create a client from the connection pool
    return Redis(connection_pool=redis_pool)

def close_redis_connections():
    """Close all connections in the Redis pool."""
    if redis_client:
        try:
            redis_client.close()
        except Exception:
            pass
    if redis_pool:
        redis_pool.disconnect()


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
        "OPENAI_API_KEY": "Required for OpenAI API access",
        "AZURE_OPENAI_ENDPOINT": "Required for Azure OpenAI service"
    }
    
    # Checking for production-specific variables
    production_vars = {
        "MONGODB_PASSWORD": "Using default password (not recommended for production)",
    }
    
    missing_critical = []
    missing_production = []
    
    for var, msg in critical_vars.items():
        if not os.getenv(var):
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
    'OPENAI_CONFIG',
    'APP_CONFIG',
    'GMAIL_CONFIG',
    'SCHEDULER_CONFIG',
    'REDIS_CONFIG',
    'PROJECT_CONFIG',
    'get_database_uri',
    'validate_config',
    'GCP_STORAGE_CONFIG'
]

