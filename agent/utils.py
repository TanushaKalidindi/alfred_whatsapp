import logging
from typing import Optional, Dict, Any, List
from bson import ObjectId
import asyncio
import datetime
import json
from .config import DATABASE_CONFIG, get_project_id
from .schemas import IWPDetectionResult, WorkPackageClassification, WorkPackageAnalysisResponse, ConflictAnalysis,IWPClassification, IWPDetectionResult
from langchain.prompts import PromptTemplate
from langchain.output_parsers import PydanticOutputParser
from langchain.schema import HumanMessage

# Configure logger
logger = logging.getLogger(__name__)

# Conflict analysis prompt template
conflict_prompt_template = """
You are a conflict detection agent. Compare the WhatsApp message content against existing task data to detect meaningful conflicts.

WhatsApp Message Content:
{message_content}

Existing Task Data:
{task_data}

Analyze if there are any meaningful conflicts between the WhatsApp message and the existing task data.
Focus on:
1. Status conflicts (e.g., message says "completed" but task shows "in progress")
2. Timeline conflicts (e.g., different completion dates)
3. Scope conflicts (e.g., different work descriptions)
4. Priority conflicts (e.g., different urgency levels)

Provide a similarity score between 0.0 (completely different) and 1.0 (identical).

{format_instructions}
"""

# Create conflict parser
conflict_parser = PydanticOutputParser(pydantic_object=ConflictAnalysis)

def get_all_site_names_and_ids(db, project_id: str) -> str:
    """
    Return site details (name, site_id, location) filtered by project_id.
    Handles project_id as ObjectId or string.
    """
    if not project_id:
        logger.error("No project_id provided to get_all_site_names_and_ids")
        return ""
        
    try:
        from bson import ObjectId
        
        # First try with project_id as ObjectId
        try:
            query = {"project_id": ObjectId(project_id)}
            sites_cursor = db[DATABASE_CONFIG["sites_db"]].find(
                query, {"_id": 1, "name": 1, "location": 1}
            )
            sites = list(sites_cursor)
            
            # If no results, try with project_id as string
            if not sites:
                query = {"project_id": str(project_id)}
                sites_cursor = db[DATABASE_CONFIG["sites_db"]].find(
                    query, {"_id": 1, "name": 1, "location": 1}
                )
                sites = list(sites_cursor)
                
        except Exception as e:
            # If ObjectId conversion fails, try with string directly
            logger.debug(f"Trying with project_id as string: {e}")
            query = {"project_id": str(project_id)}
            sites_cursor = db[DATABASE_CONFIG["sites_db"]].find(
                query, {"_id": 1, "name": 1, "location": 1}
            )
            sites = list(sites_cursor)

        if not sites:
            logger.warning(f"No sites found for project_id: {project_id}")
            return ""

        site_list = []
        for site in sites:
            try:
                site_name = site.get('name', 'Unknown')
                site_id = str(site.get('_id', ''))
                location = site.get('location', 'N/A')
                site_list.append(f"{site_name} ({site_id}) - {location}")
            except Exception as e:
                logger.error(f"Error formatting site {site.get('_id')}: {e}")
                continue
                
        if not site_list:
            logger.warning(f"No valid sites found for project_id: {project_id}")
            return ""
            
        logger.debug(f"Found {len(site_list)} sites for project_id: {project_id}")
        return ", ".join(site_list)
        
    except Exception as e:
        logger.error(f"Error in get_all_site_names_and_ids for project_id {project_id}: {e}")
        return ""

def get_assets_with_cwps_and_iwps(db, site_ids, site_names=None):
    """
    Fetch CWPs and their IWPs for one or more site_ids.
    Fixed to handle list inputs and ObjectId serialization.
    """
    # FIXED: Handle list inputs and None cases
    if isinstance(site_ids, list):
        if not site_ids:
            return []
        site_id = site_ids[0]  # Take first site_id
        logger.warning(f"get_assets_with_cwps_and_iwps received list, using first element: {site_id}")
    else:
        site_id = site_ids

    if not site_id or site_id == "null":
        logger.warning("No valid site_id provided to get_assets_with_cwps_and_iwps")
        return []

    try:
        # Convert to ObjectId
        if isinstance(site_id, str) and ObjectId.is_valid(site_id):
            site_oid = ObjectId(site_id)
        else:
            logger.error(f"Invalid site_id: {site_id}")
            return []

        # Query assets for the site
        assets = list(db[DATABASE_CONFIG["assets_db"]].find({
            "site_id": site_oid
        }, {
            "_id": 1,
            "name": 1,
            "asset_type": 1,
            "status": 1
        }))

        result = []
        for asset in assets:
            # FIXED: Convert ObjectIds to strings for JSON serialization
            asset_data = {
                "asset_id": str(asset["_id"]),
                "asset_name": asset.get("name", "Unknown Asset"),
                "asset_type": asset.get("asset_type", ""),
                "status": asset.get("status", "")
            }

            # Get CWPs for this asset
            cwps = list(db[DATABASE_CONFIG["cwp_db"]].find({
                "asset_id": asset["_id"],
                "site_id": site_oid
            }, {
                "_id": 1,
                "package_name": 1,
                "scope": 1,
                "status": 1,
                "estimated_duration": 1,
                "prerequisites": 1
            }))

            # Process CWPs and convert ObjectIds to strings
            cwp_list = []
            for cwp in cwps:
                cwp_data = {
                    "cwp_id": str(cwp["_id"]),
                    "package_name": cwp.get("package_name", ""),
                    "scope": cwp.get("scope", ""),
                    "status": cwp.get("status", ""),
                    "estimated_duration": cwp.get("estimated_duration", ""),
                    "prerequisites": cwp.get("prerequisites", "")
                }

                # Get IWPs for this CWP
                iwps = list(db[DATABASE_CONFIG["iwp_db"]].find({
                    "cwp_id": cwp["_id"]
                }, {
                    "_id": 1,
                    "package_name": 1,
                    "status": 1,
                    "estimated_duration": 1
                }))

                # Process IWPs and convert ObjectIds to strings
                iwp_list = []
                for iwp in iwps:
                    iwp_data = {
                        "iwp_id": str(iwp["_id"]),
                        "package_name": iwp.get("package_name", ""),
                        "status": iwp.get("status", ""),
                        "estimated_duration": iwp.get("estimated_duration", "")
                    }
                    iwp_list.append(iwp_data)

                cwp_data["iwps"] = iwp_list
                cwp_list.append(cwp_data)

            asset_data["cwps"] = cwp_list
            result.append(asset_data)

        logger.debug(f"Found {len(result)} assets with CWPs/IWPs for site {site_id}")
        return result

    except Exception as e:
        logger.error(f"Error in get_assets_with_cwps_and_iwps for site {site_id}: {e}")
        return []
        
def get_risks_by_site(db, site_id, project_id: Optional[str] = None) -> list[dict]:
    """
    Fetch open risks for given site_id(s) and optionally project_id.
    Works whether site_id / project_id are stored as string or ObjectId.
    Handles both single site_id (str) and multiple site_ids (list).
    """
    
    # Handle different input types for site_id
    if isinstance(site_id, list):
        if not site_id:
            logger.debug("Empty site_id list provided")
            return []
        # Process multiple site_ids
        all_risks = []
        for sid in site_id:
            if sid:  # Skip None/empty values
                single_site_risks = get_risks_by_site(db, sid, project_id=get_project_id())
                all_risks.extend(single_site_risks)
        return all_risks
    
    # Handle single site_id
    if not site_id or site_id == "null":
        logger.debug("No valid site_id provided")
        return []
        
    try:
        # Base query: only open risks
        query: Dict[str, Any] = {"status": "open"}

        # Site condition: match string or ObjectId
        site_conditions = [{"site_id": site_id}]
        if ObjectId.is_valid(site_id):
            site_conditions.append({"site_id": ObjectId(site_id)})

        query["$or"] = site_conditions

        # Project condition: optional
        if project_id:
            project_conditions = [{"project_id": project_id}]

            # Combine $or for site with $or for project using $and
            query = {"$and": [query, {"$or": project_conditions}]}

        risks = list(db[DATABASE_CONFIG["risk_db"]].find(query))

        if not risks:
            logger.debug("No open risks found for site_id=%s project_id=%s", site_id, project_id)
        else:
            logger.debug("Found %d open risks for site_id=%s", len(risks), site_id)
            
        return risks

    except Exception as e:
        logger.error(f"Error fetching risks for site {site_id}: {e}", exc_info=True)
        return []


def get_task_context_for_llm(db, site_ids, site_names):
    """
    Fetch concise task context for each site, suitable for LLM input.
    Excludes any risk information.

    Args:
        db: MongoDB database object
        site_ids (list): List of site IDs
        site_names (list): List of site names
        DATABASE_CONFIG (dict): Contains 'task_db' name
        logger: Logger instance

    Returns:
        dict: {site_name: [concise task summary strings]}
    """
    tasks_by_site = {}

    # Normalize inputs to lists
    if not isinstance(site_ids, list):
        site_ids = [site_ids]
    if not isinstance(site_names, list):
        site_names = [site_names]

    for site_id, site_name in zip(site_ids, site_names):
        task_descriptions = []

        if site_id and site_id != "null" and ObjectId.is_valid(site_id):
            try:
                # Debug: Check total tasks in collection
                total_tasks = db[DATABASE_CONFIG["iwp_db"]].count_documents({})
                logger.debug(f"Total tasks in {DATABASE_CONFIG['iwp_db']}: {total_tasks}")
                
                # Debug: Check tasks for this specific site
                site_oid = ObjectId(site_id)
                site_task_count = db[DATABASE_CONFIG["iwp_db"]].count_documents({"site_id": site_oid})
                logger.debug(f"Tasks for site {site_id}: {site_task_count}")
                
                # Also try string site_id in case it's stored as string
                site_task_count_str = db[DATABASE_CONFIG["iwp_db"]].count_documents({"site_id": site_id})
                logger.debug(f"Tasks for site {site_id} (as string): {site_task_count_str}")
                
                task_docs = db[DATABASE_CONFIG["iwp_db"]].find(
                    {"site_id": ObjectId(site_id)},
                    {"title": 1, "status": 1, "end_date": 1} 
                )

                for task in task_docs:
                    task_id = str(task["_id"])
                    task_name = task.get("title", "Unknown")
                    status = task.get("status", "unknown")
                    end_date = task.get("end_date", "N/A")

                    summary = (
                        f"Task ID: {task_id} | Task: {task_name} | Status: {status} "
                        f"| End Date: {end_date}"
                    )

                    task_descriptions.append(summary)

            except Exception as e:
                logger.error(f"Error fetching tasks for site {site_id}: {e}")

        tasks_by_site[site_name] = task_descriptions

    return tasks_by_site

def get_iwp_context_for_llm(db, site_ids, site_names):
    """
    Fetch concise IWP (Installation Work Package) context for each site, 
    suitable for LLM input. Excludes any risk information.

    Args:
        db: MongoDB database object
        site_ids (list): List of site IDs
        site_names (list): List of site names

    Returns:
        dict: {site_name: [concise IWP summary strings]}
    """
    iwps_by_site = {}

    # Normalize inputs to lists
    if not isinstance(site_ids, list):
        site_ids = [site_ids]
    if not isinstance(site_names, list):
        site_names = [site_names]

    for site_id, site_name in zip(site_ids, site_names):
        iwp_descriptions = []

        if site_id and site_id != "null" and ObjectId.is_valid(site_id):
            try:
                iwp_docs = db[DATABASE_CONFIG["iwp_db"]].find(
                    {"site_id": ObjectId(site_id)},
                    {
                        "package_name": 1,
                        "scope": 1,
                        "status": 1,
                        "start_date": 1,
                        "due_date": 1,
                    }
                )

                for iwp in iwp_docs:
                    iwp_id = str(iwp["_id"])
                    package_name = iwp.get("package_name", "Unknown Package")
                    scope = iwp.get("scope", "No scope provided")
                    status = iwp.get("status", "unknown")
                    start_date = iwp.get("start_date", "N/A")
                    due_date = iwp.get("due_date", "N/A")

                    summary = (
                        f"IWP ID: {iwp_id} | Package: {package_name} | Status: {status} "
                        f"| Start: {start_date} | Due: {due_date} | Scope: {scope}"
                    )

                    iwp_descriptions.append(summary)

            except Exception as e:
                logger.error(f"Error fetching IWPs for site {site_id}: {e}")

        iwps_by_site[site_name] = iwp_descriptions

    return iwps_by_site

def get_packages_by_site(db, site_ids, site_names):
    """Fetch package information for each site."""
    packages_by_site = {}

    if not isinstance(site_ids, list):
        site_ids = [site_ids]
    if not isinstance(site_names, list):
        site_names = [site_names]

    for site_id, site_name in zip(site_ids, site_names):
        package_list = []

        if site_id and site_id != "null" and ObjectId.is_valid(site_id):
            try:
                package_docs = db[DATABASE_CONFIG["package_db"]].find(
                    {"site_id": ObjectId(site_id)},
                    {"_id": 1, "package_name": 1, "cwp_id": 1}  # Return dict fields
                )

                for package in package_docs:
                    # Return as dictionary, not string
                    package_dict = {
                        "package_id": str(package["_id"]),
                        "package_name": package.get("package_name", "Unknown"),
                        "cwp_id": str(package.get("cwp_id", ""))
                    }
                    package_list.append(package_dict)

            except Exception as e:
                logger.error(f"Error fetching packages for site {site_id}: {e}")

        packages_by_site[site_name] = package_list

    return packages_by_site

def get_full_iwps_with_reasoning(
    db, 
    site_ids: List[str], 
    site_names: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch full IWP documents for each site that have a non-empty 'reasoning' field."""
    iwps_by_site = {}
    
    if not isinstance(site_ids, list):
        site_ids = [site_ids]
    if not isinstance(site_names, list):
        site_names = [site_names]
    
    for site_id, site_name in zip(site_ids, site_names):
        iwp_docs = []
        if site_id and site_id != "null" and ObjectId.is_valid(site_id):
            try:
                # FIX: Use the correct collection name from DATABASE_CONFIG
                cursor = db[DATABASE_CONFIG["iwp_db"]].find({  # Changed from db["iwp"]
                    "site_id": ObjectId(site_id),
                    "reasoning": {"$exists": True, "$ne": []}
                })
                iwp_docs = [doc for doc in cursor]
            except Exception as e:
                logger.error(f"Error fetching IWPs for site {site_id}: {e}")
        iwps_by_site[site_name] = iwp_docs
    
    return iwps_by_site


def detect_iwp_relationships(
    message_content: str, 
    all_iwps: List[Dict[str, Any]], 
    llm
) -> IWPDetectionResult:
    """
    Classify ALL IWPs as related (True) or not related (False) to the message.
    
    Args:
        message_content: The WhatsApp message text
        all_iwps: List of ALL IWP documents from database
        llm: Language model instance
    
    Returns:
        IWPDetectionResult with boolean classification for each IWP
    """
    parser = PydanticOutputParser(pydantic_object=IWPDetectionResult)
    
    # Format IWPs for LLM - include key fields for matching
    formatted_iwps = []
    for iwp in all_iwps:
        iwp_info = {
            "iwp_id": str(iwp.get("_id")),
            "package_name": iwp.get("package_name", ""),
            "scope": iwp.get("scope", ""),
            "status": iwp.get("status", ""),
            "reasoning": iwp.get("reasoning", "")  # Your existing reasoning field
        }
        formatted_iwps.append(iwp_info)
    
    prompt_template = PromptTemplate.from_template("""
You are an IWP relationship detection agent.

Your task: For EVERY IWP in the list below, determine if it is related to the WhatsApp message.

WhatsApp Message:
"{message_content}"

All IWPs in System:
{iwps_list}

Instructions:
1. Analyze the message content to understand what it's about
2. For EACH IWP, determine if it's related to this message (True/False)
3. Provide confidence score (0.0-1.0) and brief reasoning for each
4. Only mark as True if there's clear relevance

Output format:
{format_instructions}

IMPORTANT: You must classify ALL {iwp_count} IWPs. Do not skip any.
""")
    
    prompt_text = prompt_template.format(
        message_content=message_content,
        iwps_list=formatted_iwps,
        iwp_count=len(all_iwps),
        format_instructions=parser.get_format_instructions()
    )
    
    logger.info(f"Classifying {len(all_iwps)} IWPs for message: {message_content[:100]}...")
    
    response = llm.invoke([HumanMessage(content=prompt_text)])
    result = parser.parse(response.content if hasattr(response, "content") else str(response))
    
    # Extract related IWP IDs
    result.related_iwp_ids = [
        c.iwp_id for c in result.classifications if c.is_related
    ]
    
    logger.info(
        f"✅ Classified {len(result.classifications)} IWPs. "
        f"Found {len(result.related_iwp_ids)} related IWPs"
    )
    
    return result

async def fetch_iwp_task_data(db: Any, iwp_id: str) -> List[Dict[str, Any]]:
    """Fetch task data for a specific IWP"""
    try:
        if ObjectId.is_valid(iwp_id):
            cursor = db["alfred_iwp"].find({"_id": ObjectId(iwp_id)})
            docs = await cursor.to_list(length=None)
            return docs
        return []
    except Exception as e:
        print(f"❌ Error fetching IWP data for {iwp_id}: {e}")
        return []


def update_iwp_with_conflict(conflict: Any, db) -> bool:
    """Update IWP document with conflict details"""
    try:
        if not hasattr(conflict, "iwp_id") or not conflict.iwp_id:
            return False
            
        task_id_obj = ObjectId(conflict.iwp_id)
        collection = db["alfred_iwp"]
        
        doc = collection.find_one({"_id": task_id_obj})
        if not doc:
            return False
        
        existing_log = doc.get("data_conflict_details", {}).get("event_log", [])
        if isinstance(existing_log, dict):
            existing_log = [existing_log]
        
        event_entry = {
            "message": getattr(conflict, "message", "No message provided"),
            "timestamp": getattr(conflict, "timestamp", datetime.datetime.utcnow())
        }
        
        if getattr(conflict, "conflict", False):
            update_data = {
                "$set": {
                    "data_conflict_details": {
                        "data_conflict": True,
                        "detected_at": getattr(conflict, "timestamp", datetime.datetime.utcnow()),
                        "reasoning": getattr(conflict, "reasoning", None),
                        "similarity_score": getattr(conflict, "similarity_score", None),
                        "event_log": existing_log + [event_entry]
                    }
                }
            }
        else:
            update_data = {
                "$set": {
                    "data_conflict_details.event_log": existing_log + [event_entry]
                }
            }
        
        update_result = collection.update_one({"_id": task_id_obj}, update_data)
        return update_result.modified_count > 0
            
    except Exception as e:
        print(f"❌ Error updating IWP: {e}")
        return False


async def process_conflict_analysis_for_iwps(
    result: Any,
    message_content_str: str,
    db: Any,
    llm: Any
) -> List[bool]:
    """Process conflict analysis for multiple IWP IDs asynchronously"""
    
    iwp_ids = getattr(result, 'iwp_id', [])
    confidences = getattr(result, 'confidence', [])
    reasonings = getattr(result, 'reasoning', [])
    messages = getattr(result, 'message', [])
    
    # Ensure all are lists
    if not isinstance(iwp_ids, list):
        iwp_ids = [iwp_ids]
    if not isinstance(confidences, list):
        confidences = [confidences]
    if not isinstance(reasonings, list):
        reasonings = [reasonings]
    if not isinstance(messages, list):
        messages = [messages]
    
    # Pad shorter lists with defaults
    max_len = max(len(iwp_ids), len(confidences), len(reasonings), len(messages)) if iwp_ids else 0
    iwp_ids = iwp_ids + [None] * (max_len - len(iwp_ids))
    confidences = confidences + [0.0] * (max_len - len(confidences))
    reasonings = reasonings + ["No reasoning provided"] * (max_len - len(reasonings))
    messages = messages + ["No message"] * (max_len - len(messages))
    
    async def process_single_iwp(iwp_id: str, confidence: float, reasoning: str, message: str):
        if not iwp_id:
            return False
            
        try:
            task_data = fetch_iwp_task_data(db, iwp_id)
            if not task_data:
                logger.warning(f"No task data found for IWP {iwp_id}")
                return False
                
            # Run conflict analysis agent using WhatsApp-specific function
            conflict_result = await whatsapp_conflict_agent(
                message_content=message_content_str,
                task_data=task_data,
                llm=llm
            )
            
            # Update the iwp_id in the conflict result
            conflict_result.iwp_id = iwp_id
            
            success = update_iwp_with_conflict(conflict_result, db)
            logger.info(f"{'✅' if success else '❌'} Processed IWP {iwp_id} - Conflict: {conflict_result.conflict}")
            return success
            
        except Exception as e:
            logger.error(f"Error processing IWP {iwp_id}: {str(e)}")
            return False
    
    # Only process valid IWP IDs
    valid_tasks = [
        (iwp_id, conf, reason, msg)
        for iwp_id, conf, reason, msg in zip(iwp_ids, confidences, reasonings, messages)
        if iwp_id
    ]
    
    if not valid_tasks:
        logger.warning("No valid IWP IDs found for conflict analysis")
        return []
    
    results = await asyncio.gather(*[
        process_single_iwp(iwp_id, conf, reason, msg)
        for iwp_id, conf, reason, msg in valid_tasks
    ])
    
    success_count = sum(1 for r in results if r is True)
    logger.info(f"📊 Conflict Analysis Summary - Total: {len(results)} | Success: {success_count} | Failed: {len(results) - success_count}")
    return results

async def process_related_iwps_for_conflicts(
    detection_result: IWPDetectionResult,
    message_content: str,
    all_iwps: List[Dict[str, Any]],
    db: Any,
    llm: Any
) -> List[Dict[str, Any]]:
    """
    Process only the IWPs marked as related for conflict analysis.
    
    Args:
        detection_result: Result from detect_iwp_relationships
        message_content: Original message content
        all_iwps: All IWP documents (to fetch full context)
        db: Database connection
        llm: Language model
    
    Returns:
        List of conflict analysis results
    """
    import asyncio
    
    # Create a map for quick lookup
    iwp_map = {str(iwp.get("_id")): iwp for iwp in all_iwps}
    
    # Get classifications for related IWPs
    related_classifications = [
        c for c in detection_result.classifications if c.is_related
    ]
    
    if not related_classifications:
        logger.info("No related IWPs found, skipping conflict analysis")
        return []
    
    logger.info(f"Processing {len(related_classifications)} related IWPs for conflicts")
    
    async def analyze_single_iwp(classification: IWPClassification):
        """Analyze conflict for a single related IWP"""
        iwp_id = classification.iwp_id
        iwp_data = iwp_map.get(iwp_id)
        
        if not iwp_data:
            logger.warning(f"IWP {iwp_id} not found in map")
            return None
        
        try:
            # Run conflict analysis with full IWP context
            from .utils import whatsapp_conflict_agent, update_iwp_with_conflict
            
            conflict_result = await whatsapp_conflict_agent(
                message_content=message_content,
                task_data=iwp_data,
                llm=llm
            )
            
            # Update the IWP with conflict information
            conflict_result.iwp_id = iwp_id
            success = update_iwp_with_conflict(conflict_result, db)
            
            logger.info(
                f"{'✅' if success else '❌'} IWP {iwp_id}: "
                f"Conflict={conflict_result.conflict}, "
                f"Similarity={conflict_result.similarity_score:.2f}"
            )
            
            return {
                "iwp_id": iwp_id,
                "success": success,
                "conflict": conflict_result.conflict,
                "similarity_score": conflict_result.similarity_score,
                "reasoning": conflict_result.reasoning,
                "detection_confidence": classification.confidence
            }
            
        except Exception as e:
            logger.error(f"Error analyzing IWP {iwp_id}: {e}")
            return {
                "iwp_id": iwp_id,
                "success": False,
                "error": str(e)
            }
    
    # Process all related IWPs concurrently
    results = await asyncio.gather(*[
        analyze_single_iwp(c) for c in related_classifications
    ])
    
    # Filter out None results
    valid_results = [r for r in results if r is not None]
    
    success_count = sum(1 for r in valid_results if r.get("success"))
    conflict_count = sum(1 for r in valid_results if r.get("conflict"))
    
    logger.info(
        f"📊 Conflict Analysis Complete: "
        f"{success_count}/{len(valid_results)} successful, "
        f"{conflict_count} conflicts detected"
    )
    
    return valid_results

def fetch_iwp_task_data(db, iwp_id: str) -> Optional[Dict[str, Any]]:
    """Fetch task data for a specific IWP ID from the database."""
    try:
        if not iwp_id or not ObjectId.is_valid(iwp_id):
            logger.warning(f"Invalid IWP ID: {iwp_id}")
            return None
            
        # Fetch from IWP collection
        iwp_collection = db[DATABASE_CONFIG["iwp_db"]]
        task_data = iwp_collection.find_one({"_id": ObjectId(iwp_id)})
        
        if not task_data:
            logger.warning(f"No task data found for IWP ID: {iwp_id}")
            return None
            
        return task_data
        
    except Exception as e:
        logger.error(f"Error fetching task data for IWP {iwp_id}: {e}")
        return None

def update_iwp_with_conflict(conflict_analysis: ConflictAnalysis, db) -> bool:
    """Update IWP document with conflict analysis results."""
    try:
        iwp_collection = db[DATABASE_CONFIG["iwp_db"]]
        
        update_data = {
            "conflict_analysis": {
                "conflict": conflict_analysis.conflict,
                "similarity_score": conflict_analysis.similarity_score,
                "reasoning": conflict_analysis.reasoning,
                "message": conflict_analysis.message,
                "timestamp": conflict_analysis.timestamp,
                "updated_at": datetime.datetime.utcnow()
            }
        }
        
        result = iwp_collection.update_one(
            {"_id": ObjectId(conflict_analysis.iwp_id)},
            {"$set": update_data}
        )
        
        if result.modified_count > 0:
            logger.info(f"Updated IWP {conflict_analysis.iwp_id} with conflict analysis")
            return True
        else:
            logger.warning(f"No IWP found to update with ID: {conflict_analysis.iwp_id}")
            return False
            
    except Exception as e:
        logger.error(f"Error updating IWP {conflict_analysis.iwp_id} with conflict analysis: {e}")
        return False

async def whatsapp_conflict_agent(message_content: str, task_data: Dict[str, Any], llm) -> ConflictAnalysis:
    """
    Compare WhatsApp message content against task data and detect conflicts.
    
    Args:
        message_content: The content of the WhatsApp message as a string
        task_data: dict with task information
        llm: LLM object that supports .ainvoke()
    
    Returns:
        ConflictAnalysis object
    """
    try:
        # Convert ObjectIds and datetime objects to strings for JSON serialization
        def serialize_task_data(data):
            if isinstance(data, list):
                return [serialize_task_data(item) for item in data]
            elif isinstance(data, dict):
                return {key: serialize_task_data(value) for key, value in data.items()}
            elif isinstance(data, ObjectId):
                return str(data)
            elif isinstance(data, datetime.datetime):
                return data.isoformat()
            else:
                return data
        
        serializable_task_data = serialize_task_data(task_data)
        
        format_instructions = conflict_parser.get_format_instructions()
        prompt_text = conflict_prompt_template.format(
            message_content=message_content,
            task_data=str(serializable_task_data),  # Convert to string for template
            format_instructions=format_instructions
        )

        # Use ainvoke for async call
        response = await llm.ainvoke([HumanMessage(content=prompt_text)])
        content = response.content if hasattr(response, "content") else str(response)

        return conflict_parser.parse(content)
    except Exception as e:
        logger.error(f"Error in whatsapp_conflict_agent: {str(e)}")
        # Return a default conflict analysis on error
        return ConflictAnalysis(
            iwp_id=str(task_data.get('_id', 'unknown')),
            conflict=False,
            similarity_score=0.0,
            reasoning=f"Error during conflict analysis: {str(e)}",
            message=message_content[:100] + "..." if len(message_content) > 100 else message_content
        )
