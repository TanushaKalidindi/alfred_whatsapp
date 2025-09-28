import os
from dotenv import load_dotenv
load_dotenv()

OPENAI_CONFIG = {
    "api_key": os.getenv("OPENAI_API_KEY", "your-default-api-key"),  # Added default
    "api_version": os.getenv("OPENAI_API_VERSION", "2024-12-01-preview"),  # Added default
    "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", "https://alfredapi.openai.azure.com"),  # Added default
    "azure_deployment": os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),  # Added default
    "embedding_deployment": os.getenv("AZURE_EMBEDDING_DEPLOYMENT", "text-embedding-3-small"),
    "embedding_api_version": os.getenv("EMBEDDING_API_VERSION", "2023-05-15"),
}