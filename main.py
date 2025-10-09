from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any
import uvicorn
import json
import logging
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variable for the WhatsApp agent instance
whatsapp_agent = None

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker health checks"""
    return {
        "status": "healthy",
        "service": "whatsapp-agent",
        "agent_initialized": whatsapp_agent is not None
    }

class UnreadMessage(BaseModel):
    chat_id: str
    chat_name: str
    chat_type: str
    from_user: str = Field(..., alias="from")
    message: str
    context: List[Dict] = []                      
    timestamp: str = Field(..., alias="time")

    model_config = {
        "populate_by_name": True,
        "extra": "allow"   # ✅ allow extra fields like "to" and "type"
    }


class BatchEnvelope(BaseModel):
    messages: List[Dict[str, Any]]

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    global whatsapp_agent
    try:
        # Import here to avoid circular imports
        from agent.main import create_agent
        whatsapp_agent = await create_agent()
        logger.info("WhatsApp agent initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize WhatsApp agent: {e}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup services on shutdown"""
    global whatsapp_agent
    if whatsapp_agent is not None:
        # WhatsAppAgent doesn't have cleanup method, just set to None
        whatsapp_agent = None
        logger.info("WhatsApp agent cleaned up successfully")

def format_for_agent(msg: UnreadMessage) -> Dict:
    """Format message for the agent's process_messages method"""
    return {
        'message_id': f"{msg.chat_id}_{msg.timestamp}",
        'from': msg.from_user,
        'timestamp': msg.timestamp,
        'text': msg.message,
        'type': 'text'
    }

@app.post("/process-whatsapp-messages")
async def process_whatsapp_messages(batch: BatchEnvelope):
    """Process WhatsApp messages through the agent.
    Accepts two shapes:
    - List of full UnreadMessage objects
    - List of batch entries with a `context` array (current Go format)
    """
    try:
        print(f"✅ Received batch from Go agent: {len(batch.messages)} items")
        print(batch.messages)

        # Normalize incoming payload into a list of UnreadMessage
        normalized: List[UnreadMessage] = []
        for item in batch.messages:
            # Case 1: already an UnreadMessage-like dict (has message/from)
            if isinstance(item, dict) and ("message" in item and ("from" in item or "from_user" in item)):
                try:
                    normalized.append(UnreadMessage.model_validate(item))
                except Exception as e:
                    logger.warning(f"Skipping invalid message item: {e}")
                continue

            # Case 2: batch entry with context array
            if isinstance(item, dict) and "context" in item:
                chat_id = item.get("chat_id")
                chat_name = item.get("chat_name")
                chat_type = item.get("chat_type")
                context_list = item.get("context") or []
                for ctx in context_list:
                    # ctx comes from Go struct; keys are capitalized
                    try:
                        normalized.append(
                            UnreadMessage(
                                chat_id=chat_id or ctx.get("To", ""),
                                chat_name=chat_name or ctx.get("ChatName", ""),
                                chat_type=chat_type or ctx.get("ChatType", ""),
                                from_user=ctx.get("From") or ctx.get("from") or "",
                                message=ctx.get("Message") or ctx.get("message") or "",
                                timestamp=ctx.get("Timestamp") or ctx.get("time") or "",
                            )
                        )
                    except Exception as e:
                        logger.warning(f"Skipping invalid context message: {e}")
                continue

            logger.warning("Unknown item shape in messages; skipping")

        if not normalized:
            raise HTTPException(status_code=400, detail="No valid messages found in payload")

        # Log message details for debugging
        for msg in normalized:
            print(f"Processing: {msg.from_user} -> {msg.message[:50]}...")

        # Initialize agent if not already done
        global whatsapp_agent
        if whatsapp_agent is None:
            from agent.main import create_agent
            whatsapp_agent = await create_agent()
            logger.info("WhatsApp agent initialized successfully")

        # Format messages for the agent
        formatted_messages = [format_for_agent(msg) for msg in normalized]

        # Process messages using the agent
        logger.info(f"Processing {len(formatted_messages)} messages through agent")
        result = whatsapp_agent.process_messages(formatted_messages)

        # Log the result
        logger.info(f"Agent processing completed. Status: {result.get('status', 'unknown')}")

        return {
            "status": "success",
            "received": len(normalized),
            "agent_result": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Error processing messages: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
