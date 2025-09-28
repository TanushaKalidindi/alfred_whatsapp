import logging
import sys
import os
import json
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime

# Add the current directory to path for local imports
sys.path.append(os.path.dirname(os.path.abspath(__name__)))

from .workflow import create_whatsapp_workflow
from .startup import initialize_dependencies
from .utils import get_all_site_names_and_ids
from .config import DATABASE_CONFIG, PROJECT_CONFIG, get_project_id

logger = logging.getLogger(__name__)

class WhatsAppAgent:
    """WhatsApp agent that processes and responds to WhatsApp messages."""
    
    def __init__(self, llm=None, db=None, sites_string: str = None, project_id: str = None):
        """
        Initialize the WhatsApp agent with required services.
        
        Args:
            llm: Language model instance for natural language processing
            db: Database connection for data persistence
            sites_string: Pre-fetched sites data (optional)
            project_id: Optional project ID to filter sites
        """
        self.llm = llm
        self.db = db
        self.project_id = project_id
        self.sites_string = sites_string
        self.workflow = None
        
    async def initialize(self):
        """Async initialization method."""
        try:
            # Initialize core services
            if self.db is None or self.llm is None:
                logger.info("Initializing dependencies...")
                self.db, self.llm, _, _ = initialize_dependencies()
                logger.info("Dependencies initialized successfully")
            
            # Ensure project_id is set from config if not provided
            if not self.project_id:
                self.project_id = get_project_id()

            # Initialize site data
            self.sites_string = self._initialize_sites(self.sites_string)
            
            # Initialize workflow with project_id
            self.workflow = create_whatsapp_workflow(self.llm, self.db, project_id=self.project_id)
            logger.info("WhatsApp agent initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize WhatsApp agent: {e}")
            raise
    
    def _initialize_sites(self, sites_string: Optional[str] = None) -> str:
        """Initialize site data from database if not provided."""
        if sites_string is not None:
            return sites_string
            
        logger.info("Fetching sites from database...")
        project_id = get_project_id()
        if not project_id:
            logger.warning("Project ID not configured")
            return ""
            
        sites = get_all_site_names_and_ids(self.db, project_id)
        if not sites:
            logger.warning("No sites found in database")
            return ""
            
        return sites
    
    def process_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process incoming WhatsApp webhook data.
        
        Args:
            webhook_data: Raw webhook data from WhatsApp
            
        Returns:
            Dict containing processing results and status
        """
        try:
            # Parse webhook data into WhatsApp messages
            messages = self._parse_webhook(webhook_data)
            if not messages:
                return self._create_error_response("No valid messages found in webhook data")
                
            # Process the messages
            return self.process_messages(messages)
            
        except Exception as e:
            error_msg = f"Error processing webhook: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return self._create_error_response(error_msg)
    
    def _parse_webhook(self, webhook_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse webhook data into a list of message dictionaries."""
        try:
            # Handle different webhook formats
            entry = webhook_data.get('entry', [{}])[0]
            changes = entry.get('changes', [{}])[0]
            value = changes.get('value', {})
            
            messages = []
            for msg in value.get('messages', []):
                message = {
                    'message_id': msg.get('id'),
                    'from': msg.get('from'),
                    'timestamp': msg.get('timestamp'),
                    'text': self._extract_message_text(msg),
                    'type': msg.get('type')
                }
                messages.append(message)
                
            return messages
            
        except Exception as e:
            logger.error(f"Error parsing webhook: {str(e)}")
            return []
    
    def _extract_message_text(self, msg: Dict[str, Any]) -> str:
        """Extract text from different message types."""
        msg_type = msg.get('type')
        if msg_type == 'text':
            return msg.get('text', {}).get('body', '')
        elif msg_type in ['image', 'document', 'audio']:
            return f"[{msg_type.upper()}] {msg.get(msg_type, {}).get('caption', '')}"
        return ""
    
    def process_messages(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Process a list of WhatsApp messages through the workflow.
        
        Args:
            messages: List of message dictionaries with chat and message data
            
        Returns:
            Dict containing processing results, status, and response messages
        """
        if not messages:
            return self._create_error_response("No messages provided")
            
        try:
            # Prepare the initial state for the workflow
            state = {
                "whatsapp_messages": messages,
                "sites_string": self.sites_string,
                "extraction_result": None,
                "site_ids": [],
                "site_names": [],
                "actions": [],
                "chat_id": messages[0].get('from') if messages else None,
                "From": messages[0].get('from') if messages else None,
                "confidence": 0.0,
                "reasoning": "",
                "context": {},
                "tasks_for_site": {},
                "packages_for_site": {},
                "risks_for_site": {},
                "iwps": {},
                "action_parameters": {},
                "parameter_extraction_results": [],
                "execution_results": [],
                "error": None,
                "response_messages": []
            }
            
            # Execute the workflow
            logger.info(f"Executing workflow for {len(messages)} messages")
            result = self.workflow.invoke(state)
            
            # Format the response
            return self._format_response(result)
            
        except Exception as e:
            error_msg = f"Error processing messages: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return self._create_error_response(error_msg)
    
    def _format_response(self, workflow_result: Dict[str, Any]) -> Dict[str, Any]:
        """Format the workflow result into a standardized response."""
        try:
            response = {
                "status": "success",
                "timestamp": datetime.utcnow().isoformat(),
                "messages_processed": len(workflow_result.get("whatsapp_messages", [])),
                "actions_identified": len(workflow_result.get("actions", [])),
                "actions_executed": len([r for r in workflow_result.get("execution_results", []) 
                                      if r.get("status") == "success"]),
                "actions_failed": len([r for r in workflow_result.get("execution_results", []) 
                                     if r.get("status") == "failed"]),
                "details": {
                    "actions": workflow_result.get("actions", []),
                    "sites": workflow_result.get("site_names", []),
                    "execution_results": workflow_result.get("execution_results", [])
                },
                "response_messages": workflow_result.get("response_messages", [])
            }
            
            # Add error information if present
            if workflow_result.get("error"):
                response["status"] = "partial" if response["actions_executed"] > 0 else "failed"
                response["error"] = str(workflow_result["error"])
                
            return response
            
        except Exception as e:
            logger.error(f"Error formatting response: {str(e)}")
            return self._create_error_response(f"Error formatting response: {str(e)}")
    
    def _create_error_response(self, error_message: str) -> Dict[str, Any]:
        """Create a standardized error response."""
        return {
            "status": "error",
            "timestamp": datetime.utcnow().isoformat(),
            "error": error_message,
            "messages_processed": 0,
            "actions_identified": 0,
            "actions_executed": 0,
            "actions_failed": 0,
            "details": {},
            "response_messages": [{
                "type": "text",
                "content": "Sorry, I encountered an error processing your request. Please try again later."
            }]
        }
    
    def generate_response(self, workflow_result: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Generate response messages based on workflow results.
        
        Args:
            workflow_result: The result from the workflow execution
            
        Returns:
            List of response message dictionaries with 'type' and 'content' keys
        """
        try:
            responses = []
            
            # Add confirmation for successful actions
            success_results = [r for r in workflow_result.get("execution_results", []) 
                             if r.get("status") == "success"]
            
            if success_results:
                success_message = "I've processed your request. "
                success_message += ", ".join([r.get("message", "") for r in success_results if r.get("message")])
                responses.append({
                    "type": "text",
                    "content": success_message
                })
            
            # Add error messages if any
            error_results = [r for r in workflow_result.get("execution_results", []) 
                           if r.get("status") == "failed"]
            
            if error_results:
                error_message = "I encountered some issues: "
                error_message += "; ".join([r.get("error", "Unknown error") for r in error_results])
                responses.append({
                    "type": "text",
                    "content": error_message
                })
            
            # If no specific responses were generated, provide a default response
            if not responses:
                responses.append({
                    "type": "text",
                    "content": "I've processed your message. Is there anything else I can help with?"
                })
            
            return responses
            
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            return [{
                "type": "text",
                "content": "I'm sorry, but I encountered an error processing your request."
            }]


async def create_agent() -> WhatsAppAgent:
    """
    Create and initialize a WhatsApp agent with default configuration.
    
    Returns:
        Initialized WhatsAppAgent instance
    """
    try:
        logger.info("Creating WhatsApp agent...")
        agent = WhatsAppAgent()
        await agent.initialize()
        logger.info("WhatsApp agent created successfully")
        return agent
    except Exception as e:
        logger.error(f"Failed to create WhatsApp agent: {str(e)}")
        raise


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('whatsapp_agent.log')
        ]
    )
    
    async def main():
        try:
            # Create and run the agent
            agent = await create_agent()
            
            # Example usage with sample message
            sample_message = [{
                'message_id': 'sample_msg_123',
                'from': 'whatsapp:+1234567890',
                'timestamp': '2025-09-24T12:00:00Z',
                'text': 'papaL SUB-STATION  Solar Power Generating System has rains',
                'type': 'text'
            }]
            
            result = agent.process_messages(sample_message)
            print("Processing result:", json.dumps(result, indent=2))
            
        except Exception as e:
            logger.error(f"Application error: {str(e)}", exc_info=True)
            print(f"An error occurred: {str(e)}")
            sys.exit(1)

    # Run the async main function
    asyncio.run(main())