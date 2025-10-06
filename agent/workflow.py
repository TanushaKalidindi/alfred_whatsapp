import logging
from langgraph.graph import StateGraph, END
from .schemas import WhatsAppState
from .nodes import (
    context_extraction_node,
    work_package_classification_node,
    parameter_extraction_node,
    action_execution_node,
    task_detection_and_conflict_node
)

logger = logging.getLogger(__name__)

def create_whatsapp_workflow(llm, db, project_id=None):
    """Create the WhatsApp agent workflow.
    
    Args:
        llm: The language model to use
        db: Database connection
        project_id: Optional project ID to filter sites
    """
    logger.info("Creating WhatsApp workflow")
    
    # Create workflow with state
    workflow = StateGraph(WhatsAppState)
    
    # Configuration to pass to nodes
    config = {
        "llm": llm, 
        "db": db,
        "project_id": project_id
    }
    
    # Add nodes with configuration
    def wrap_node(node_func):
        def wrapper(state):
            # Ensure state is a dictionary
            if not isinstance(state, dict):
                state = dict(state)
            # Ensure db is in state, if not add it from config
            if 'db' not in state or state['db'] is None:
                state['db'] = db
            return node_func(state, config)
        return wrapper
    
    workflow.add_node("extract_context", 
                     wrap_node(context_extraction_node))
    
    workflow.add_node("task_detection", 
                     wrap_node(task_detection_and_conflict_node))

    workflow.add_node("classify_work_packages", 
                     wrap_node(work_package_classification_node))
    
    workflow.add_node("extract_parameters", 
                     wrap_node(parameter_extraction_node))
    
    workflow.add_node("execute_actions", 
                     wrap_node(action_execution_node))
    
    # Set entry point
    workflow.set_entry_point("extract_context")
    
    # Add conditional routing
    workflow.add_conditional_edges(
        "extract_context",
        route_after_context_extraction,
        {
            "task_detection": "task_detection",
            "end": END
        }
    )
    
    # Task detection runs first to get related tasks/conflicts
    workflow.add_edge("task_detection", "classify_work_packages")
    workflow.add_edge("classify_work_packages", "extract_parameters")
    workflow.add_edge("extract_parameters", "execute_actions")
    workflow.add_edge("execute_actions", END)
    
    return workflow.compile()

def route_after_context_extraction(state: WhatsAppState) -> str:
    """Route based on context extraction results."""
    
    # Check for errors
    if state.get("error"):
        logger.warning(f"Error in context extraction: {state['error']}")
        return "end"
    
    # Check extraction result
    extraction_result = state.get("extraction_result")
    if not extraction_result:
        logger.warning("No extraction result found")
        return "end"
    
    # Check confidence level
    if extraction_result.confidence < 0.6:
        logger.info(f"Low confidence ({extraction_result.confidence}), ending workflow")
        return "end"
    
    # Check if we have actionable items
    actions = extraction_result.actions
    if not actions or all(action.lower() in ["irrelevant"] for action in actions):
        logger.info("No actionable items found")
        return "end"
    
    # Continue to Task detection
    logger.info("Routing to Task detection")
    return "task_detection"