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
    - Flat list of UnreadMessage objects
    - Go batch entries with `context_messages` and `new_messages`
    """
    try:
        logger.info(f"✅ Received batch from Go agent: {len(batch.messages)} items")

        normalized: List[UnreadMessage] = []
        for item in batch.messages:
            if not isinstance(item, dict):
                continue

            # Case 1: Direct UnreadMessage-style payload
            if "message" in item and ("from" in item or "from_user" in item):
                try:
                    normalized.append(UnreadMessage.model_validate(item))
                    continue
                except Exception as e:
                    logger.warning(f"Skipping invalid message: {e}")
                    continue

            # Case 2: Go agent payload with context_messages/new_messages
            if "context_messages" in item or "new_messages" in item:
                chat_id = item.get("chat_id", "")
                chat_name = item.get("chat_name", "")
                chat_type = item.get("chat_type", "")

                context_messages = item.get("context_messages", [])
                new_messages = item.get("new_messages", [])

                for ctx in context_messages + new_messages:
                    try:
                        normalized.append(
                            UnreadMessage(
                                chat_id=chat_id,
                                chat_name=chat_name,
                                chat_type=chat_type,
                                from_user=ctx.get("from", ""),
                                message=ctx.get("message", ""),
                                timestamp=ctx.get("timestamp", ctx.get("time", "")),
                            )
                        )
                    except Exception as e:
                        logger.warning(f"Skipping invalid sub-message: {e}")
                continue

            # Case 3: Legacy format
            if "context" in item:
                for ctx in item.get("context", []):
                    try:
                        normalized.append(
                            UnreadMessage(
                                chat_id=item.get("chat_id", ctx.get("To", "")),
                                chat_name=item.get("chat_name", ctx.get("ChatName", "")),
                                chat_type=item.get("chat_type", ctx.get("ChatType", "")),
                                from_user=ctx.get("From") or ctx.get("from") or "",
                                message=ctx.get("Message") or ctx.get("message") or "",
                                timestamp=ctx.get("Timestamp") or ctx.get("time") or "",
                            )
                        )
                    except Exception as e:
                        logger.warning(f"Skipping invalid legacy message: {e}")
                continue

            logger.warning(f"⚠️ Unknown item shape in messages; skipping: {json.dumps(item)[:150]}")

        if not normalized:
            raise HTTPException(status_code=400, detail="No valid messages found in payload")

        # Log summary
        logger.info(f"📩 Normalized {len(normalized)} messages total")
        for msg in normalized[-3:]:  # preview last few
            logger.info(f"   {msg.chat_name} | {msg.from_user}: {msg.message[:60]}...")

        # Initialize agent if missing
        global whatsapp_agent
        if whatsapp_agent is None:
            from agent.main import create_agent
            whatsapp_agent = await create_agent()
            logger.info("WhatsApp agent initialized successfully")

        formatted_messages = [format_for_agent(msg) for msg in normalized]
        logger.info(f"⚙️ Sending {len(formatted_messages)} messages to agent")

        result = whatsapp_agent.process_messages(formatted_messages)

        logger.info(f"✅ Agent processed messages successfully")

        return {
            "status": "success",
            "received": len(normalized),
            "agent_result": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error processing messages")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=True)
