from pymongo import MongoClient
from qdrant_client import QdrantClient
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings
from .config import DATABASE_CONFIG, OPENAI_CONFIG, QDRANT_CONFIG, get_database_uri

import logging
logger = logging.getLogger(__name__)

def get_database():
    client = MongoClient(get_database_uri())
    return client[DATABASE_CONFIG['database']]

def initialize_dependencies():
    logger.info("Initializing dependencies...")

    db = get_database()
    logger.info("MongoDB connected successfully")

    llm = AzureChatOpenAI(
        azure_deployment=OPENAI_CONFIG['azure_deployment'],
        api_version=OPENAI_CONFIG['api_version'],
        azure_endpoint=OPENAI_CONFIG['azure_endpoint'],
        api_key=OPENAI_CONFIG['api_key'],
        temperature=0,
        max_tokens=None,
        timeout=30,  # Add timeout
        max_retries=3  # Add retry logic
    )
    logger.info("Azure OpenAI LLM initialized successfully")

    embedding_model = AzureOpenAIEmbeddings(
        azure_deployment=OPENAI_CONFIG['embedding_deployment'],
        openai_api_key=OPENAI_CONFIG['api_key'],
        openai_api_version=OPENAI_CONFIG['embedding_api_version'],
        azure_endpoint=OPENAI_CONFIG['azure_endpoint'],
        chunk_size=1000
    )
    logger.info("Embedding model initialized successfully")

    qdrant_client = QdrantClient(
        url=QDRANT_CONFIG['url'],
        api_key=QDRANT_CONFIG['api_key']
    )

    return db, llm, embedding_model, qdrant_client
