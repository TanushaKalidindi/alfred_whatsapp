from pymongo import MongoClient
from qdrant_client import QdrantClient
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from .config import DATABASE_CONFIG, GOOGLE_CONFIG, QDRANT_CONFIG, get_database_uri

import logging
logger = logging.getLogger(__name__)

def get_database():
    client = MongoClient(get_database_uri())
    return client[DATABASE_CONFIG['database']]

def initialize_dependencies():
    logger.info("Initializing dependencies...")

    db = get_database()
    logger.info("MongoDB connected successfully")

    # Validate Google/Gemini API key early to avoid runtime 400s
    api_key = GOOGLE_CONFIG.get('api_key')
    if not api_key:
        setup_vars = [
            'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_GENAI_API_KEY',
            'GOOGLE_GEMINI_API_KEY', 'GENAI_API_KEY'
        ]
        raise RuntimeError(
            "Google/Gemini API key is not configured. Set one of: "
            + ", ".join(setup_vars) +
            ". You can add it to your environment or a .env file."
        )

    llm = ChatGoogleGenerativeAI(
        model=GOOGLE_CONFIG['model'],
        google_api_key=api_key,
        temperature=0.5,
        max_output_tokens=1024
    )
    logger.info("Google LLM initialized successfully")

    embedding_model = GoogleGenerativeAIEmbeddings(
        model="models/embedding-001",          # Gemini embedding model
        google_api_key=api_key,
        task_type="retrieval_document"         # Optional but improves relevance
    )
    embedding_model_name: str = "gemini-embedding-001"
    logger.info(f"Embedding model '{embedding_model_name}' initialized successfully")


    qdrant_client = QdrantClient(
        url=QDRANT_CONFIG['url'],
        api_key=QDRANT_CONFIG['api_key']
    )

    return db, llm, embedding_model, qdrant_client
