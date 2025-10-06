from typing import List, Dict, Any, Optional, TypedDict, Union,Literal
from pydantic import BaseModel, Field
from enum import Enum
from pymongo.database import Database
import datetime

class WhatsAppState(TypedDict):
    """State container for WhatsApp message processing.
    
    This class holds the complete state of a WhatsApp conversation, including message history,
    extracted information, and processing results.
    """
    # Core message data
    whatsapp_messages: List[Dict] = Field(
        ...,
        description="List of WhatsApp message objects in the current conversation"
    )
    sites_string: str = Field(
        ...,
        description="String containing all site-related information extracted from messages"
    )
    
    # Database connection
    db: Any = Field(
        None,
        description="Database connection instance for data persistence"
    )
    
    # Site extraction results
    extraction_result: Optional['SiteIdExtraction'] = Field(
        None,
        description="Result of site ID extraction from messages"
    )
    site_ids: Optional[List[str]] = Field(
        None,
        description="List of unique site identifiers mentioned in the conversation"
    )
    site_names: Optional[List[str]] = Field(
        None,
        description="Human-readable names of sites mentioned in the conversation"
    )
    
    # Message metadata
    actions: Optional[List[str]] = Field(
        None,
        description="List of actions to be performed based on message content"
    )
    chat_id: Optional[List[str]] = Field(
        None,
        description="Unique identifier(s) for the WhatsApp chat/group"
    )
    From: Optional[str] = Field(
        None,
        description="Phone number or identifier of the message sender"
    )
    
    # Processing state
    confidence: Optional[float] = Field(
        None,
        description="Confidence score (0-1) of the current processing state"
    )
    reasoning: Optional[str] = Field(
        None,
        description="Explanation of the current processing state or decision"
    )
    context: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional context for message processing"
    )
    
    # Site-specific data
    tasks_for_site: Optional[Dict[str, List[Dict]]] = Field(
        None,
        description="Tasks associated with each site"
    )
    packages_for_site: Optional[Dict[str, List[Dict]]] = Field(
        None,
        description="Work packages associated with each site"
    )
    risks_for_site: Optional[Dict[str, List[Dict]]] = Field(
        None,
        description="Risks identified for each site"
    )
    iwps: Optional[Dict[str, Any]] = Field(
        None,
        description="Integrated Work Packages data structure"
    )
    
    # Action processing
    action_parameters: Optional[Dict[str, BaseModel]] = Field(
        None,
        description="Parameters for executing actions"
    )
    parameter_extraction_results: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Results of parameter extraction from messages"
    )
    execution_results: Optional[List[Dict[str, Any]]] = Field(
        None,
        description="Results of action executions"
    )
    executed_actions: Optional[Dict[str, Any]] = Field(
        None,
        description="Mapping of action names to their execution results"
    )
    
    # Error handling
    error: Optional[str] = Field(
        None,
        description="Description of any error that occurred during processing"
    )

class SiteIdExtraction(BaseModel):
    """Extracted site information from WhatsApp messages.
    
    This class represents the result of parsing and extracting site-related
    information from incoming messages.
    """
    site_ids: Optional[List[Optional[str]]] = Field(
        default=None,
        description="List of site IDs extracted from the message, if any"
    )
    site_names: Optional[List[Optional[str]]] = Field(
        default=None,
        description="List of site names extracted from the message, if any"
    )
    actions: List[str] = Field(
        ...,
        description="List of actions identified in the message"
    )
    chat_id: List[str] = Field(
        ...,
        description="List of chat IDs associated with the message"
    )
    From: str = Field(
        ...,
        description="Sender's phone number or identifier"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score of the extraction (0.0 to 1.0)"
    )
    reasoning: str = Field(
        ...,
        description="Explanation of how the extraction was performed"
    )

class Risk(BaseModel):
    """Represents a risk identified in the system.
    
    This class models potential risks associated with construction sites or projects,
    including their severity, status, and category.
    """
    site_id: str = Field(
        ...,
        description="Unique identifier of the site this risk is associated with"
    )
    title: str = Field(
        ...,
        max_length=200,
        description="Brief title summarizing the risk"
    )
    description: str = Field(
        ...,
        description="Detailed description of the risk"
    )
    severity: str = Field(
        ...,
        description="Severity level of the risk (e.g., low, medium, high, critical)"
    )
    status: str = Field(
        default="open",
        description="Current status of the risk (e.g., open, in_progress, resolved)"
    )
    category: str = Field(
        ...,
        description="Category of the risk (e.g., safety, environmental, financial)"
    )

class AddRiskInput(BaseModel):
    risks: List[Risk] = Field(...)
    title: str = Field(...)
    summary: str = Field(...)
    reasoning: str = Field(...)

class Task(BaseModel):
    task_id: str = Field(...)
    status: str = Field(...)
    notes: Optional[str] = Field(default=None)
    completion_percentage: Optional[int] = Field(default=None)

class UpdateTaskInput(BaseModel):
    tasks: List[Task] = Field(...)
    summary: str = Field(...)
    title: str = Field(...)
    reasoning: str = Field(...)

class RiskUpdate(BaseModel):
    """Update information for an existing risk.
    
    This class is used to update the status and other properties of an existing risk.
    """
    risk_id: str = Field(
        ...,
        description="Unique identifier of the risk to update"
    )
    status: str = Field(
        ...,
        description="New status to set for the risk"
    )
    notes: Optional[str] = Field(
        default=None,
        description="Additional notes or comments about the update"
    )
    severity: Optional[str] = Field(
        default=None,
        description="Updated severity level if changed"
    )

class UpdateRiskInput(BaseModel):
    """Input model for updating multiple risks at once.
    
    This class bundles multiple risk updates with additional metadata
    about the update operation.
    """
    risks: List[RiskUpdate] = Field(
        ...,
        description="List of risk updates to apply"
    )
    summary: str = Field(
        ...,
        description="Brief summary of the changes being made"
    )
    title: str = Field(
        ...,
        description="Title for this batch of risk updates"
    )
    reasoning: str = Field(
        ...,
        description="Explanation of why these updates are being made"
    )

class RiskStatus(str, Enum):
    """Possible status values for a risk.
    
    Attributes:
        OPEN: The risk has been identified but not yet addressed
        MITIGATED: Mitigation measures have been put in place
        RESOLVED: The risk has been fully resolved
        CLOSED: The risk is no longer relevant or has been accepted
    """
    OPEN = "open"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    CLOSED = "closed"

class TaskStatus(str, Enum):
    """Possible status values for a task.
    
    Attributes:
        OPEN: The task has been created but work has not started
        IN_PROGRESS: Work on the task is currently in progress
        COMPLETED: The task has been successfully completed
    """
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class updateResponse(BaseModel):
    """Standardized response format for update operations.
    
    This model provides a consistent structure for reporting the results of
    various update operations throughout the system.
    """
    title: str = Field(
        ...,
        description=(
            "General title for this batch of risk additions. "
            "Keep it short (3–6 words), like a news headline. "
            "Example: 'Weather-Related Delays Identified'."
        ),
    )
    message: str = Field(..., description="Detailed message of the update")
    type: Literal["data_conflict", "actionable", "update", "rfi", "compliance", "decision", "general", "risk"] = Field(..., description="Type of the update")
    status: Literal["open", "acknowledged", "action_required", "resolved", "closed"] = Field(..., description="Status of the update")
    related_entity_type: Optional[str] = Field(None, description="Type of related entity (e.g., 'task', 'risk', 'asset')")
    related_entity_id: Optional[str] = Field(None, description="ID of the related entity")
    action_taken: Literal["action_required", "decision"] = Field(..., description="Type of action taken. Must be one of: 'action_required' or 'decision'")
    action_description: str = Field(..., description="Detailed description of the action that was taken or needs to be taken")


class WorkPackageClassification(BaseModel):
    """Classification of which work packages are affected by an action.
    
    This model tracks how different work packages (both CWPs and IWPs) are
    impacted by a particular action or event in the system.
    """
    action_name: str = Field(
        ...,
        description="Name of the action that affects the work packages"
    )
    affected_cwps: List[str] = Field(
        default_factory=list,
        description="List of Contract Work Package (CWP) IDs affected by the action"
    )
    affected_iwps: List[str] = Field(
        default_factory=list,
        description="List of Integrated Work Package (IWP) IDs affected by the action"
    )
    primary_cwp: Optional[str] = Field(
        None,
        description="Primary CWP most significantly impacted by this action"
    )
    primary_iwp: Optional[str] = Field(
        None,
        description="Primary IWP most significantly impacted by this action"
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score (0.0 to 1.0) of the classification accuracy"
    )
    reasoning: str = Field(
        default="",
        description="Explanation of how the classification was determined"
    )
    impact_level: str = Field(
        default="low",
        description="Impact level of the action on the work packages (low, medium, high)",
        pattern="^(low|medium|high)$"
    )

class WorkPackageAnalysisResponse(BaseModel):
    """Response containing work package classifications for all actions"""
    classifications: List[WorkPackageClassification]
    summary: str = ""

class IWPClassification(BaseModel):
    """Classification result for a single IWP"""
    iwp_id: str = Field(..., description="IWP ID being classified")
    is_related: bool = Field(..., description="True if IWP is related to the message")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score")
    reasoning: str = Field(..., description="Brief reasoning for classification")

class IWPDetectionResult(BaseModel):
    """Complete detection result with all IWP classifications"""
    classifications: List[IWPClassification] = Field(
        default_factory=list, 
        description="Classification for each IWP"
    )
    related_iwp_ids: List[str] = Field(
        default_factory=list,
        description="List of IWP IDs that are related (is_related=True)"
    )
    message_summary: str = Field(..., description="Summary of what the message is about")

class ConflictAnalysis(BaseModel):
    """Analysis result for conflicts between old and new data"""
    iwp_id: str = Field(..., description="IWP ID being analyzed")
    conflict: bool = Field(..., description="True if there is a meaningful conflict")
    similarity_score: float = Field(..., ge=0.0, le=1.0, description="Similarity score between 0.0 and 1.0")
    reasoning: str = Field(..., description="Explanation of the conflict decision")
    message: str = Field(..., description="Associated message")
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, description="Timestamp of analysis")


class TaskDetectionResult(BaseModel):
    """Detection result of Task IDs referenced in a message."""
    task_id: List[str] = Field(default_factory=list, description="Detected Task IDs")
    confidence: List[float] = Field(default_factory=list, description="Confidence per detected Task")
    reasoning: List[str] = Field(default_factory=list, description="Reasoning per detected Task")
    message: List[str] = Field(default_factory=list, description="Associated snippet/message per Task")


class TaskConflictAnalysis(BaseModel):
    """Conflict analysis result for a Task."""
    task_id: str = Field(..., description="Task ID being analyzed")
    conflict: bool = Field(..., description="True if there is a meaningful conflict")
    similarity_score: float = Field(..., ge=0.0, le=1.0, description="Similarity score between 0.0 and 1.0")
    reasoning: str = Field(..., description="Explanation of the conflict decision")
    message: str = Field(..., description="Associated message")
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.utcnow, description="Timestamp of analysis")

