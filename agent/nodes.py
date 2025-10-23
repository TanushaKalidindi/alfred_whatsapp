import logging
import json
import datetime
import re
from typing import Dict, Any
from langchain.schema import HumanMessage
from langchain.output_parsers import PydanticOutputParser
from langchain.prompts import PromptTemplate
from .startup import get_database

from .schemas import (
    WhatsAppState, SiteIdExtraction, 
    AddRiskInput, UpdateTaskInput, UpdateRiskInput,
    WorkPackageClassification, WorkPackageAnalysisResponse,
    updateResponse, IWPDetectionResult,
    TaskDetectionResult, TaskConflictAnalysis
)
from .templates import (
    context_prompt_template, risk_prompt_template, 
    task_prompt_template, risk_update_prompt_template,
    task_detection_prompt_template
)
from .db_utils import add_risk, update_task, update_risk, update_response_in_db
from .db_utils import fetch_task_data as db_fetch_task_data, update_task_with_conflict as db_update_task_with_conflict
from .utils import (
    get_risks_by_site, 
    get_all_site_names_and_ids,
    get_task_context_for_llm,
    get_task_context_for_conflicts,
    get_iwp_context_for_llm,
    get_packages_by_site,
    detect_iwp_relationships,
    process_related_iwps_for_conflicts,
)
from .config import DATABASE_CONFIG, get_project_id

logger = logging.getLogger(__name__)

# Parsers
risk_parser = PydanticOutputParser(return_id=False, pydantic_object=AddRiskInput)
task_parser = PydanticOutputParser(return_id=False, pydantic_object=UpdateTaskInput)
risk_update_parser = PydanticOutputParser(return_id=False, pydantic_object=UpdateRiskInput)
context_parser = PydanticOutputParser(return_id=False, pydantic_object=SiteIdExtraction)


def detect_task_id(email_content_str: str, tasks: str, llm) -> TaskDetectionResult:
    """Detect Task ID(s) from WhatsApp content using LLM."""
    parser = PydanticOutputParser(pydantic_object=TaskDetectionResult)
    format_instructions = parser.get_format_instructions()
    prompt_text = task_detection_prompt_template.format(
        email_content_str=email_content_str,
        tasks=json.dumps(tasks, indent=2),
        format_instructions=format_instructions
    )
    response = llm.invoke([HumanMessage(content=prompt_text)])
    content = response.content if hasattr(response, "content") else str(response)
    return parser.parse(content)


async def data_conflict_agent(email_content: str, task_data: Dict[str, Any], llm) -> TaskConflictAnalysis:
    """
    Compare WhatsApp message against task data and detect conflicts (task-centric).
    Returns TaskConflictAnalysis.
    """
    from langchain.output_parsers import PydanticOutputParser
    from .schemas import TaskConflictAnalysis
    from langchain.schema import HumanMessage
    # Reuse the generic conflict prompt from utils-like style
    conflict_prompt_template = (
        "You are a conflict detection agent. Compare the WhatsApp message content against existing task data to detect meaningful conflicts.\n\n"
        "WhatsApp Message Content:\n{email_content}\n\n"
        "Existing Task Data:\n{task_data}\n\n"
        "Analyze conflicts: status/timeline/scope/priority. Provide similarity score 0.0-1.0.\n\n{format_instructions}"
    )
    # Serialize task_data for prompt
    def serialize(data):
        if isinstance(data, list):
            return [serialize(x) for x in data]
        if isinstance(data, dict):
            return {k: serialize(v) for k, v in data.items()}
        try:
            import datetime as _dt
            from bson import ObjectId as _OID
            if isinstance(data, _OID):
                return str(data)
            if isinstance(data, _dt.datetime):
                return data.isoformat()
        except Exception:
            pass
        return data
    serializable_task_data = serialize(task_data)
    parser = PydanticOutputParser(pydantic_object=TaskConflictAnalysis)
    prompt_text = conflict_prompt_template.format(
        email_content=email_content,
        task_data=json.dumps(serializable_task_data, indent=2),
        format_instructions=parser.get_format_instructions()
    )
    response = await llm.ainvoke([HumanMessage(content=prompt_text)])
    content = response.content if hasattr(response, "content") else str(response)
    return parser.parse(content)


async def process_conflict_analysis_for_tasks(
    result: Any,
    email_content_str: str,
    db: Any,
    llm: Any
) -> list:
    """Process conflict analysis for multiple Task IDs asynchronously."""

    task_ids = getattr(result, 'task_id', [])
    confidences = getattr(result, 'confidence', [])
    reasonings = getattr(result, 'reasoning', [])
    messages = getattr(result, 'message', [])

    # Ensure lists
    if not isinstance(task_ids, list):
        task_ids = [task_ids]
    if not isinstance(confidences, list):
        confidences = [confidences]
    if not isinstance(reasonings, list):
        reasonings = [reasonings]
    if not isinstance(messages, list):
        messages = [messages]

    max_len = max(len(task_ids), len(confidences), len(reasonings), len(messages)) if task_ids else 0
    task_ids += [None] * (max_len - len(task_ids))
    confidences += [None] * (max_len - len(confidences))
    reasonings += [None] * (max_len - len(reasonings))
    messages += [None] * (max_len - len(messages))

    async def process_single(task_id: str, _c, _r, _m):
        if not task_id:
            return False
        try:
            task_doc = db_fetch_task_data(db, task_id)
            if not task_doc:
                logger.warning(f"No task data found for Task {task_id}")
                return False
            conflict_result = await data_conflict_agent(
                email_content=email_content_str,
                task_data=task_doc,
                llm=llm
            )
            # Ensure task_id present
            conflict_result.task_id = task_id
            success = db_update_task_with_conflict(conflict_result, db)
            logger.info(f"{'✅' if success else '❌'} Processed Task {task_id}")
            return success
        except Exception as e:
            logger.error(f"Error processing Task {task_id}: {e}")
            return False

    tasks = [process_single(tid, c, r, m) for tid, c, r, m in zip(task_ids, confidences, reasonings, messages) if tid]
    if not tasks:
        return []
    import asyncio as _asyncio
    results = await _asyncio.gather(*tasks, return_exceptions=False)
    return results


def task_detection_and_conflict_node(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph node for Task detection and conflict analysis from WhatsApp content.
    """
    logger.info("Starting Task Detection & Conflict Analysis Node...")
    try:
        # Compose WhatsApp content string
        whatsapp_messages = state.get("whatsapp_messages", [])
        email_content_str = "\n".join([
            (m.get("text") or m.get("message") or (m.get("body", {}) if isinstance(m.get("text"), dict) else "")) if isinstance(m, dict) else str(m)
            for m in whatsapp_messages
        ])

        db = state.get("db")
        if db is None:
            db = get_database()
        llm = config.get("llm")

        site_ids = state.get("site_ids", [])
        site_names = state.get("site_names", [])

        if not llm:
            state["task_error"] = "LLM not available"
            return state

        # Step 1: Get Tasks with reasoning/audit context
        tasks = get_task_context_for_conflicts(db, site_ids, site_names)
        state["tasks"] = tasks

        # Step 2: Detect Task IDs
        if isinstance(tasks, dict):
            formatted_tasks = ""
            for sname, tlist in tasks.items():
                formatted_tasks += f"Site: {sname}\n"
                if tlist:
                    for task_string in tlist:
                        formatted_tasks += f"  - {task_string}\n"
                else:
                    formatted_tasks += "  - No tasks found with audit logs\n"
        else:
            formatted_tasks = str(tasks)

        detection_result = detect_task_id(email_content_str, formatted_tasks, llm)
        state["task_detection_result"] = detection_result

        # Step 3: Conflict analysis for detected tasks
        # Run async helper synchronously
        def _run(coro):
            import concurrent.futures, asyncio as _asyncio
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_asyncio.run, coro)
                return fut.result()

        conflict_results = _run(
            process_conflict_analysis_for_tasks(
                detection_result,
                email_content_str,
                db,
                llm,
            )
        )
        state["task_conflict_analysis_results"] = conflict_results

        logger.info("✅ Task Detection & Conflict Analysis Node Complete!")
        return state

    except Exception as e:
        error_msg = f"[TASK_NODE] Error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        state["task_error"] = error_msg
        return state

def extract_site_info_llm(email_content: str, sites_string: str, llm) -> IWPDetectionResult:
    """Extract site information from email content using LLM"""
    try:
        parser = PydanticOutputParser(pydantic_object=IWPDetectionResult)

        site_extraction_template = """
You are a site information extraction agent.
Your job is to analyze the email content and identify which sites are mentioned.

Email Content:
{email_content}

Available Sites:
{sites_string}

Extract:
- iwp_id: The ID(s) of sites mentioned in the email
- confidence: How confident you are about the extraction (0.0-1.0)
- reasoning: Explanation of your decision
- message: Associated message content

Output format:
{format_instructions}
"""

        prompt = PromptTemplate.from_template(site_extraction_template)
        formatted_prompt = prompt.format(
            email_content=email_content,
            sites_string=sites_string,
            format_instructions=parser.get_format_instructions()
        )

        response = llm.invoke([HumanMessage(content=formatted_prompt)])
        result = parser.parse(response.content if hasattr(response, 'content') else str(response))

        return result

    except Exception as e:
        logger.error(f"Error in extract_site_info_llm: {str(e)}")
        return IWPDetectionResult(
            iwp_id=[],
            confidence=[0.0],
            reasoning=[f"Error: {str(e)}"],
            message=[""]
        )

def extract_whatsapp_context(whatsapp_messages: list, sites_string: str, llm) -> SiteIdExtraction:
    """Extract context from WhatsApp messages."""
    try:
        logger.info("Generating context extraction prompt...")
        logger.info(f"Messages being processed: {whatsapp_messages}")
        logger.info(f"Sites being sent to LLM: {sites_string}")
        prompt = context_prompt_template.format(
            messages=whatsapp_messages,
            sites=sites_string,
            format_instructions=context_parser.get_format_instructions()
        )
        
        logger.info("Sending prompt to LLM...")
        response = llm.invoke([HumanMessage(content=prompt)])
        print(response)
        logger.info(f"LLM response received. Content length: {len(response.content) if response.content else 0}")
        logger.info(f"LLM raw response: {response.content}")
        
        logger.debug(f"Parsing LLM response: {response.content[:200]}...")
        
        # First try normal parsing
        try:
            result = context_parser.parse(response.content)
            logger.info(f"Context extraction successful. Actions: {result.actions}, Site IDs: {result.site_ids}")
            return result
        except Exception as parse_error:
            # If parsing fails, try to fix nested arrays in the JSON
            logger.warning(f"Initial parsing failed, attempting to fix nested arrays: {parse_error}")
            
            import json
            import re
            
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
            if json_match:
                json_data = json.loads(json_match.group())
                
                # Fix nested arrays for site_ids and site_names
                if 'site_ids' in json_data and json_data['site_ids']:
                    fixed_site_ids = []
                    for item in json_data['site_ids']:
                        if isinstance(item, list) and len(item) > 0:
                            fixed_site_ids.append(item[0])  # Take first element
                        else:
                            fixed_site_ids.append(item)
                    json_data['site_ids'] = fixed_site_ids
                
                if 'site_names' in json_data and json_data['site_names']:
                    fixed_site_names = []
                    for item in json_data['site_names']:
                        if isinstance(item, list) and len(item) > 0:
                            fixed_site_names.append(item[0])  # Take first element
                        else:
                            fixed_site_names.append(item)
                    json_data['site_names'] = fixed_site_names

                # Also fix nested arrays for chat_id which should be a list[str]
                if 'chat_id' in json_data and json_data['chat_id']:
                    fixed_chat_ids = []
                    for item in json_data['chat_id']:
                        if isinstance(item, list) and len(item) > 0:
                            fixed_chat_ids.append(item[0])  # Take first element
                        else:
                            fixed_chat_ids.append(item)
                    json_data['chat_id'] = fixed_chat_ids
                
                # Try parsing again with fixed data
                result = SiteIdExtraction(**json_data)
                logger.info(f"Context extraction successful after fixing arrays. Actions: {result.actions}, Site IDs: {result.site_ids}")
                return result
            else:
                raise parse_error
                
    except Exception as e:
        logger.error(f"Error in extract_whatsapp_context: {str(e)}")
        logger.error("Full traceback:", exc_info=True)
        # Return a default result with empty values
        return SiteIdExtraction(
            site_ids=[],
            site_names=[],
            actions=["irrelevant"],
            chat_id=["unknown"],
            From="unknown",
            confidence=0.0,
            reasoning=f"Failed to parse context: {str(e)}"
        )

def context_extraction_node(state: WhatsAppState, config: Dict[str, Any]) -> WhatsAppState:
    """Extract context from WhatsApp messages."""
    try:
        logger.info("Starting context extraction")
        
        db = get_database()
        if db is None:
            error_msg = "Failed to get database connection from startup"
            logger.error(error_msg)
            return {**state, "error": error_msg}
            
        project_id = get_project_id()
        print(project_id)
        logger.info(f"Checking sites for project_id: {project_id}")
        
        try:
            sites_collection = db[DATABASE_CONFIG["sites_db"]]
            total_sites = sites_collection.count_documents({"project_id": project_id})
            sample_sites = list(sites_collection.find(
                {"project_id": project_id}, 
                {"_id": 1, "name": 1, "location": 1}
            ))
            
            if sample_sites:
                site_examples = []
                for site in sample_sites:
                    site_name = site.get('name', 'Unknown')
                    site_id = str(site.get('_id', ''))
                    location = site.get('location', 'N/A')
                    site_examples.append(f"{site_name} ({site_id}) - {location}")
                
                sites_string = f"Total sites: {total_sites}. Examples: " + ", ".join(site_examples)
            else:
                sites_string = "No sites found"
                
            logger.info(f"Found {total_sites} total sites, using {len(sample_sites)} examples for context")
            
        except Exception as e:
            error_msg = f"Error fetching sites: {str(e)}"
            logger.error(error_msg)
            sites_string = "Error loading sites"
        
        whatsapp_messages = state.get("whatsapp_messages", [])
        if not whatsapp_messages:
            error_msg = "No WhatsApp messages provided in state"
            logger.warning(error_msg)
            return {**state, "error": error_msg}
            
        llm = config.get("llm")
        if not llm:
            error_msg = "No LLM instance provided in config"
            logger.error(error_msg)
            return {**state, "error": error_msg}
        
        logger.info("Extracting context from messages...")
        extraction_result = extract_whatsapp_context(whatsapp_messages, sites_string, llm)
        
        actions = getattr(extraction_result, 'actions', [])
        if not actions:
            logger.warning("No actions found in extraction result, defaulting to 'update'")
            actions = ["update"]
            
        chat_id = getattr(extraction_result, 'chat_id', ["default_chat"])
        From = getattr(extraction_result, 'From', "unknown")
        confidence = getattr(extraction_result, 'confidence', 0.0)
        reasoning = getattr(extraction_result, 'reasoning', 'No reasoning provided')
        site_ids = getattr(extraction_result, 'site_ids', [])
        site_names = getattr(extraction_result, 'site_names', [])
        
        context = {
            "reasoning": reasoning,
            "site_ids": site_ids,
            "site_names": site_names,
            "chat_id": chat_id,
            "From": From,
            "new_messages": whatsapp_messages,
        }
        
        logger.info(f"Extracted {len(actions)} actions: {actions}")
        logger.debug(f"Extraction result: {extraction_result}")
        
        new_state = {
            **state,
            "extraction_result": extraction_result,
            "site_ids": site_ids,
            "site_names": site_names,
            "actions": actions,
            "chat_id": chat_id,
            "From": From,
            "confidence": confidence,
            "reasoning": reasoning,
            "context": context,
            "db": db,
            "error": None
        }
        
        logger.debug(f"Context extraction completed. State keys: {new_state.keys()}")
        return new_state
        
    except Exception as e:
        error_msg = f"Context extraction failed: {str(e)}"
        logger.error(error_msg)
        logger.exception("Full traceback:")
        return {
            **state, 
            "error": error_msg,
            "actions": ["update"],
            "site_ids": [],
            "site_names": [],
            "confidence": 0.0,
            "reasoning": f"Error: {str(e)}"
        }

def work_package_classification_node(state: WhatsAppState, config: Dict[str, Any]) -> WhatsAppState:
    """Classify work packages and create structured analysis of work package impacts."""
    try:
        logger.info("Starting work package classification")
        
        site_ids = state.get("site_ids", [])
        db = get_database()
        
        if not site_ids or db is None:
            return {
                **state,
                "work_package_analysis": WorkPackageAnalysisResponse(
                    classifications=[],
                    summary="No valid site IDs or database connection available"
                ).dict(),
                "error": "Missing site IDs or database connection"
            }
        
        tasks_for_site = {}
        packages_for_site = {}
        risks_for_site = {}
        iwps = {}
        classifications = []
        
        for idx, site_id in enumerate(site_ids):
            if not site_id:
                continue
            
            site_name = None
            site_names = state.get('site_names', [])
            if site_names and idx < len(site_names):
                site_name = site_names[idx]
            
            site_risks = get_risks_by_site(db, site_id)
            risks_for_site[site_id] = site_risks
            
            if site_name:
                try:
                    tasks_data = get_task_context_for_llm(db, [site_id], [site_name])
                    site_tasks = tasks_data.get(site_name, [])
                    tasks_for_site[site_id] = site_tasks
                    
                    packages_data = get_packages_by_site(db, [site_id], [site_name])
                    site_packages = packages_data.get(site_name, [])
                    
                    if isinstance(site_packages, str):
                        site_packages = []
                        logger.warning(f"Packages data for site {site_name} returned as string, using empty list")
                    elif not isinstance(site_packages, list):
                        site_packages = []
                        logger.warning(f"Packages data for site {site_name} is not a list, using empty list")
                    
                    packages_for_site[site_id] = site_packages
                    
                    affected_cwps = []
                    primary_cwp = None
                    
                    for pkg in site_packages:
                        if isinstance(pkg, dict):
                            cwp_id = pkg.get('cwp_id')
                            if cwp_id:
                                affected_cwps.append(cwp_id)
                                if primary_cwp is None:
                                    primary_cwp = cwp_id
                        else:
                            logger.warning(f"Package item is not a dictionary: {type(pkg)}")
                    
                    classification = WorkPackageClassification(
                        action_name="site_analysis",
                        affected_cwps=affected_cwps,
                        affected_iwps=[],
                        primary_cwp=primary_cwp,
                        confidence=0.9,
                        reasoning=f"Analyzed {len(site_tasks)} tasks and {len(site_packages)} packages for site {site_name}",
                        impact_level="medium"
                    )
                    classifications.append(classification)
                    
                    iwps[site_id] = {
                        "tasks": site_tasks,
                        "packages": site_packages,
                        "risks": site_risks,
                        "site_id": site_id,
                        "site_name": site_name
                    }
                    
                    logger.info(f"Classified work packages for site {site_name} (ID: {site_id}): "
                             f"{len(site_tasks)} tasks, {len(site_packages)} packages, {len(site_risks)} risks")
                    
                except Exception as e:
                    logger.error(f"Error processing site {site_id}: {e}", exc_info=True)
                    error_classification = WorkPackageClassification(
                        action_name="error_processing_site",
                        affected_cwps=[],
                        affected_iwps=[],
                        confidence=0.0,
                        reasoning=f"Error processing site {site_id}: {str(e)}",
                        impact_level="low"
                    )
                    classifications.append(error_classification)
                    
                    tasks_for_site[site_id] = []
                    packages_for_site[site_id] = []
                    iwps[site_id] = {
                        "tasks": [],
                        "packages": [],
                        "risks": site_risks,
                        "site_id": site_id,
                        "site_name": site_name
                    }
            else:
                logger.warning(f"No site name found for site ID: {site_id}")
                tasks_for_site[site_id] = []
                packages_for_site[site_id] = []
                iwps[site_id] = {
                    "tasks": [],
                    "packages": [],
                    "risks": site_risks,
                    "site_id": site_id
                }
        
        analysis_response = WorkPackageAnalysisResponse(
            classifications=classifications,
            summary=f"Analyzed {len(site_ids)} sites with {len(classifications)} work package classifications"
        )
        
        new_state = {
            **state,
            "tasks_for_site": tasks_for_site,
            "packages_for_site": packages_for_site,
            "risks_for_site": risks_for_site,
            "iwps": iwps,
            "work_package_analysis": analysis_response.dict(),
            "db": db,
            "error": None
        }
        
        logger.info(f"Work package classification completed for {len(site_ids)} sites")
        try:
            logger.debug(f"Work package analysis: {analysis_response.model_dump()}")
        except AttributeError:
            logger.debug(f"Work package analysis: {analysis_response.dict()}")
        
        return new_state
        
    except Exception as e:
        error_msg = f"Work package classification failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        return {
            **state,
            "work_package_analysis": WorkPackageAnalysisResponse(
                classifications=[],
                summary=error_msg
            ).dict(),
            "error": error_msg
        }

def parameter_extraction_node(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract parameters for each action."""
    
    if not isinstance(state, dict):
        state = dict(state)
    
    try:
        logger.info("Starting parameter extraction")
        
        actions = state.get("actions", [])
        context = state.get("context", {})
        llm = config.get("llm")
        
        db = state.get("db")
        if db is None:
            db = get_database()
            if db is None:
                error_msg = "No database connection available for parameter extraction"
                logger.error(error_msg)
                return {
                    **state,
                    "action_parameters": {},
                    "parameter_extraction_results": [{
                        "status": "failed",
                        "error": error_msg
                    }],
                    "error": error_msg
                }
            else:
                state["db"] = db
                logger.info("Database connection established in parameter extraction")
        
        if not actions:
            logger.info("No actions found, skipping parameter extraction")
            return {
                **state,
                "action_parameters": {},
                "parameter_extraction_results": [{
                    "status": "skipped",
                    "message": "No actions to process"
                }],
                "error": None
            }
        
        if not llm:
            error_msg = "No LLM instance available for parameter extraction"
            logger.error(error_msg)
            return {
                **state,
                "action_parameters": {},
                "parameter_extraction_results": [{
                    "status": "failed",
                    "error": error_msg
                }],
                "error": error_msg
            }
        
        action_parameters = {}
        parameter_extraction_results = []
        
        logger.info(f"Processing {len(actions)} actions: {actions}")
        
        for action in actions:
            try:
                normalized_action = action.lower().replace(" ", "_")
                logger.info(f"Processing action: {action} -> {normalized_action}")
                
                if normalized_action == "add_risk":
                    logger.info("Extracting risk parameters...")
                    result = extract_risk_parameters(context, llm, db)
                    action_parameters[normalized_action] = result
                    logger.info(f"Risk parameters extracted: {len(result.risks)} risks")
                    
                elif normalized_action == "update_task":
                    logger.info("Extracting task parameters...")
                    result = extract_task_parameters(context, llm)
                    action_parameters[normalized_action] = result
                    logger.info(f"Task parameters extracted: {len(result.tasks)} tasks")
                    
                elif normalized_action == "update_risk":
                    logger.info("Extracting risk update parameters...")
                    result = extract_risk_update_parameters(context, llm, db)
                    action_parameters[normalized_action] = result
                    logger.info(f"Risk update parameters extracted: {len(result.risks)} risks")
                
                elif normalized_action == "update":
                    logger.info("Extracting generic update parameters using LLM...")
                    # Use LLM to extract proper parameters instead of hardcoded response
                    result = extract_update_parameters(context, llm)
                    action_parameters[normalized_action] = result
                    logger.info("Generic update parameters extracted using LLM")
                
                else:
                    logger.warning(f"Unknown action: {normalized_action}")
                    parameter_extraction_results.append({
                        "action": action,
                        "status": "skipped",
                        "message": f"Unknown action type: {normalized_action}"
                    })
                    continue
                
                parameter_extraction_results.append({
                    "action": action,
                    "status": "success",
                    "message": f"Parameters extracted for {action}"
                })
                
            except Exception as e:
                error_msg = f"Parameter extraction failed for {action}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                parameter_extraction_results.append({
                    "action": action,
                    "status": "failed",
                    "error": error_msg
                })
        
        return {
            **state,
            "action_parameters": action_parameters,
            "parameter_extraction_results": parameter_extraction_results,
            "db": db,
            "error": None
        }
        
    except Exception as e:
        error_msg = f"Parameter extraction node failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {
            **state,
            "action_parameters": {},
            "parameter_extraction_results": [{
                "status": "failed",
                "error": error_msg
            }],
            "error": error_msg
        }


def extract_update_parameters(context: Dict[str, Any], llm) -> updateResponse:
    """Extract update parameters using LLM instead of hardcoded response."""
    try:
        # Create a template for generic updates
        update_prompt_template = """
Based on the following WhatsApp conversation, extract update information:

WhatsApp Messages: {new_messages}
From: {From}
Reasoning: {reasoning}

Analyze the message and extract:
- title: Brief descriptive title for this update (3-6 words)
- message: Detailed message content from the WhatsApp conversation
- type: Determine the most appropriate type from: data_conflict, actionable, update, rfi, compliance, decision, general, risk
- status: Determine appropriate status from: open, acknowledged, action_required, resolved, closed
- action_taken: Either "action_required" or "decision"
- action_description: Detailed description of what action was taken or needs to be taken

Return the response in this exact JSON format:
{{
    "title": "Brief title here",
    "message": "Detailed message content",
    "type": "update",
    "status": "acknowledged",
    "action_taken": "decision",
    "action_description": "Description of action"
}}
"""
        
        prompt = update_prompt_template.format(
            new_messages=json.dumps(context.get("new_messages", []), indent=2),
            From=context.get("From", ""),
            reasoning=context.get("reasoning", "")
        )
        
        logger.debug(f"Update extraction prompt: {prompt[:500]}...")
        response = llm.invoke([HumanMessage(content=prompt)])
        
        # Parse the JSON response
        try:
            # Try to extract JSON from the response
            import re
            json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
            if json_match:
                response_data = json.loads(json_match.group())
            else:
                # Fallback parsing if no clear JSON block
                response_data = json.loads(response.content)
            
            # Create updateResponse object with extracted data
            result = updateResponse(
                title=response_data.get("title", "General Update"),
                message=response_data.get("message", context.get("new_messages", [{}])[0].get("text", "No message content")),
                type=response_data.get("type", "update"),
                status=response_data.get("status", "acknowledged"),
                action_taken=response_data.get("action_taken", "decision"),
                action_description=response_data.get("action_description", "Processed update message from WhatsApp")
            )
            
            logger.info(f"Successfully parsed update parameters using LLM: {result.title}")
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}")
            logger.debug(f"Raw LLM response: {response.content}")
            # Fallback to extracting information manually
            return create_fallback_update_response(context)
            
    except Exception as e:
        logger.error(f"Error in extract_update_parameters: {str(e)}", exc_info=True)
        return create_fallback_update_response(context)


def create_fallback_update_response(context: Dict[str, Any]) -> updateResponse:
    """Create a fallback update response when LLM extraction fails."""
    message_text = ""
    if context.get("new_messages"):
        message_text = context["new_messages"][0].get("text", "No message content")
    
    # Try to intelligently determine the type based on message content
    message_lower = message_text.lower()
    
    if any(word in message_lower for word in ["risk", "danger", "issue", "problem"]):
        msg_type = "risk"
        status = "action_required"
        action_taken = "action_required"
    elif any(word in message_lower for word in ["complete", "finished", "done"]):
        msg_type = "update"
        status = "resolved"
        action_taken = "decision"
    elif any(word in message_lower for word in ["delay", "postpone", "schedule"]):
        msg_type = "actionable"
        status = "action_required"
        action_taken = "action_required"
    else:
        msg_type = "general"
        status = "acknowledged"
        action_taken = "decision"
    
    return updateResponse(
        title=f"Update from {context.get('From', 'Unknown')}",
        message=message_text,
        type=msg_type,
        status=status,
        action_taken=action_taken,
        action_description=f"Processed {msg_type} message from WhatsApp"
    )

def action_execution_node(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the actual actions using db_utils functions."""
    logger.info("Executing action execution node")
    
    if not isinstance(state, dict):
        state = dict(state)
    
    try:
        db = get_database()
        if db is None:
            error_msg = "Failed to get database connection from startup"
            logger.error(error_msg)
            return {
                **state,
                "execution_results": [{
                    "status": "failed",
                    "error": error_msg,
                    "details": {
                        "action_parameters_received": bool(state.get("action_parameters")),
                        "db_connected": False
                    }
                }],
                "executed_actions": {}
            }
        
        action_parameters = state.get("action_parameters", {})
        if not action_parameters:
            logger.warning("No action parameters found in state")
            return {
                **state,
                "execution_results": [{
                    "status": "skipped",
                    "message": "No actions to execute"
                }],
                "executed_actions": {}
            }
            
        from_number = state.get("From", "")
        site_ids = state.get("site_ids", [])
        has_site_id = bool(site_ids)
        
        logger.info(f"Action parameters received: {action_parameters}")
        logger.info(f"Available actions: {list(action_parameters.keys())}")
        
        if db is None:
            error_msg = "Database connection is not available"
            logger.error(error_msg)
            return {
                **state,
                "execution_results": [{
                    "status": "failed",
                    "error": error_msg,
                    "details": {
                        "action_parameters_received": bool(action_parameters),
                        "db_connected": False,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        "error_type": "DatabaseConnectionError"
                    }
                }],
                "executed_actions": {},
                "error": error_msg
            }
            
        try:
            db.command('ping')
            logger.info("Database connection verified")
        except Exception as e:
            error_msg = f"Database connection test failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                **state,
                "execution_results": [{
                    "status": "failed",
                    "error": error_msg,
                    "details": {
                        "action_parameters_received": bool(action_parameters),
                        "db_connected": False,
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        "error_type": type(e).__name__
                    }
                }],
                "executed_actions": {},
                "error": error_msg
            }
        
        if not has_site_id:
            logger.info("No site_id provided, only allowing add_risk and update_task actions")
            filtered_actions = {}
            for action_name, params in action_parameters.items():
                if action_name in ["add_risk", "update_task", "update"]:
                    filtered_actions[action_name] = params
                else:
                    logger.warning(f"Skipping {action_name} action as it requires a site_id")
            
            if not filtered_actions and action_parameters:
                return {
                    **state,
                    "execution_results": [{
                        "status": "failed",
                        "error": "No valid actions available (site_id required for the requested actions)"
                    }],
                    "executed_actions": {}
                }
                
            action_parameters = filtered_actions
        
        execution_results = []
        executed_actions = {}
        
        for action_name, parameters in action_parameters.items():
            try:
                if action_name == "add_risk":
                    risks_data = []
                    logger.info(f"Preparing to add {len(parameters.risks)} risks")
                    
                    for i, risk in enumerate(parameters.risks, 1):
                        try:
                            risk_data = {
                                "site_id": risk.site_id,
                                "description": risk.description,
                                "severity": risk.severity.value if hasattr(risk.severity, 'value') else risk.severity,
                                "impact": getattr(risk, 'impact', 'medium'),
                                "mitigation_plan": getattr(risk, 'mitigation_plan', ''),
                                "reasoning": getattr(risk, 'reasoning', ''),
                                "status": "open"
                            }
                            logger.debug(f"Risk {i} data: {risk_data}")
                            risks_data.append(risk_data)
                        except Exception as e:
                            logger.error(f"Error processing risk {i}: {e}", exc_info=True)
                            continue
                    
                    if not risks_data:
                        raise ValueError("No valid risks to add after processing")
                    
                    try:
                        logger.info(f"Calling add_risk with {len(risks_data)} risks")
                        logger.debug(f"Database connection: {db is not None}")
                        
                        result = add_risk(
                            risks=risks_data,
                            summary=parameters.summary,
                            title=parameters.title,
                            db=db,
                            From=from_number
                        )
                        
                        execution_result = {
                            'action': action_name,
                            'status': 'completed',
                            'message': str(result) if result else 'Risk added successfully',
                            'details': {
                                'timestamp': datetime.datetime.utcnow().isoformat(),
                                'action_type': action_name,
                                'risks_processed': len(risks_data)
                            }
                        }
                        
                        execution_results.append(execution_result)
                        
                        executed_actions[action_name] = {
                            'result': str(result) if result else 'Success',
                            'executed_at': datetime.datetime.utcnow().isoformat(),
                            'status': 'completed'
                        }
                        
                    except Exception as e:
                        error_msg = f"Failed to add risks: {str(e)}"
                        logger.error(error_msg, exc_info=True)
                        execution_results.append({
                            "action": action_name,
                            "status": "failed",
                            "error": error_msg,
                            "details": str(e)
                        })
                
                elif action_name == "update_task":
                    tasks_data = []
                    for task in parameters.tasks:
                        tasks_data.append({
                            "task_id": task.task_id,
                            "status": task.status,
                            "notes": task.notes,
                            "reasoning": task.reasoning,
                            "completion_percentage": task.completion_percentage,
                        })
                    
                    # Get work package data from state
                    work_package_data = state.get("work_package_analysis", {})
                    
                    result = update_task(
                        tasks=tasks_data,
                        summary=parameters.summary,
                        title=parameters.title,
                        From=from_number,
                        db=db,
                        work_package_data=work_package_data  # Add this line
                    )
                    
                    execution_result = {
                        'action': action_name,
                        'status': 'completed',
                        'message': str(result) if result else 'Task updated successfully',
                        'details': {
                            'timestamp': datetime.datetime.utcnow().isoformat(),
                            'action_type': action_name,
                            'tasks_processed': len(tasks_data)
                        }
                    }
                    
                    execution_results.append(execution_result)
                    
                    executed_actions[action_name] = {
                        'result': str(result) if result else 'Success',
                        'executed_at': datetime.datetime.utcnow().isoformat(),
                        'status': 'completed'
                    }
                
                elif action_name == "update_risk":
                    risks_data = []
                    for risk_update in parameters.risks:
                        risks_data.append({
                            "risk_id": risk_update.risk_id,
                            "status": risk_update.status,
                            "notes": risk_update.notes,
                            "severity": risk_update.severity,
                            "mitigation_plan": getattr(risk_update, 'mitigation_plan', ''),
                            "reasoning": getattr(risk_update, 'reasoning', ''),
                        })
                    
                    result = update_risk(
                        risks=risks_data,
                        summary=parameters.summary,
                        title=parameters.title,
                        From=from_number,
                        db=db
                    )
                    
                    execution_result = {
                        'action': action_name,
                        'status': 'completed',
                        'message': str(result) if result else 'Risk updated successfully',
                        'details': {
                            'timestamp': datetime.datetime.utcnow().isoformat(),
                            'action_type': action_name,
                            'risks_processed': len(risks_data)
                        }
                    }
                    
                    execution_results.append(execution_result)
                    
                    executed_actions[action_name] = {
                        'result': str(result) if result else 'Success',
                        'executed_at': datetime.datetime.utcnow().isoformat(),
                        'status': 'completed'
                    }
                
                elif action_name == "update":
                    logger.info("Executing generic update action...")
                    try:
                        result = update_response_in_db(
                            type=parameters.type,
                            message=parameters.message,
                            title=parameters.title,
                            status=parameters.status,
                            action_taken=parameters.action_taken,
                            From=from_number,
                            db=db
                        )
                        
                        execution_result = {
                            'action': action_name,
                            'status': 'completed',
                            'message': 'Generic update processed successfully',
                            'details': {
                                'title': parameters.title,
                                'type': parameters.type,
                                'timestamp': datetime.datetime.utcnow().isoformat(),
                                'action_type': action_name
                            }
                        }
                        
                        execution_results.append(execution_result)
                        
                        executed_actions[action_name] = {
                            'result': result,
                            'executed_at': datetime.datetime.utcnow().isoformat(),
                            'status': 'completed'
                        }
                        
                    except Exception as e:
                        error_msg = f"Failed to process generic update: {str(e)}"
                        logger.error(error_msg, exc_info=True)
                        execution_results.append({
                            "action": action_name,
                            "status": "failed",
                            "error": error_msg,
                            "details": str(e)
                        })
                
            except Exception as e:
                logger.error(f"Action execution failed for {action_name}: {e}")
                execution_results.append({
                    "action": action_name,
                    "status": "failed",
                    "message": f"Execution failed: {str(e)}"
                })
        
        return {
            **state,
            "execution_results": execution_results,
            "executed_actions": executed_actions,
            "error": None
        }
        
    except Exception as e:
        logger.error(f"Action execution node failed: {str(e)}")
        return {**state, "error": f"Action execution failed: {str(e)}"}

def extract_risk_parameters(context: Dict[str, Any], llm, db) -> AddRiskInput:
    """Extract risk parameters using LLM."""
    try:
        site_ids = context.get("site_ids", [])
        primary_site_id = site_ids[0] if site_ids else None
        site_names = context.get("site_names", [])
        primary_site_name = site_names[0] if site_names else None
        
        risks = []
        if db is not None and primary_site_id is not None:
            try:
                risks = get_risks_by_site(db, primary_site_id)
                logger.info(f"Found {len(risks)} existing risks for site {primary_site_id}")
            except Exception as e:
                logger.error(f"Failed to fetch risks for site {primary_site_id}: {str(e)}")
                risks = []
        
        prompt = risk_prompt_template.format(
            new_messages=json.dumps(context.get("new_messages", []), indent=2),
            available_sites=primary_site_id or "No site specified",
            site_names=primary_site_name or "No site name",
            From=context.get("From", ""),
            available_tasks="{}",
            available_risks=json.dumps(risks, indent=2, default=str),
            reasoning=context.get("reasoning", ""),
            format_instructions=risk_parser.get_format_instructions()
        )
        
        logger.debug(f"Risk extraction prompt: {prompt[:500]}...")
        response = llm.invoke([HumanMessage(content=prompt)])
        result = risk_parser.parse(response.content)
        logger.info(f"Successfully parsed risk parameters: {len(result.risks)} risks")
        return result
        
    except Exception as e:
        logger.error(f"Error in extract_risk_parameters: {str(e)}", exc_info=True)
        from agent.schemas import AddRiskInput, Risk
        return AddRiskInput(
            risks=[],
            title="Error extracting risk parameters",
            summary=f"Failed to extract risk parameters: {str(e)}",
            reasoning=f"Error: {str(e)}"
        )


def extract_task_parameters(context: Dict[str, Any], llm) -> UpdateTaskInput:
    """Extract task parameters using LLM."""
    try:
        # Get site IDs from context
        site_ids = context.get("site_ids", [])
        
        # Query database directly for actual tasks
        enhanced_tasks = {}
        if site_ids:
            db = get_database()
            if db is not None:  # FIXED: Use 'is not None' instead of 'if db:'
                for site_id in site_ids:
                    if site_id:
                        try:
                            from bson import ObjectId
                            tasks_collection = db[DATABASE_CONFIG["task_db"]]
                            
                            # Get actual task documents with the fields you showed
                            task_docs = list(tasks_collection.find(
                                {"site_id": ObjectId(site_id)},
                                {
                                    "_id": 1,
                                    "title": 1, 
                                    "status": 1,
                                    "start_date": 1,
                                    "due_date": 1,
                                    "description": 1
                                }
                            ).limit(20))
                            
                            # Format for LLM with your actual field names
                            site_tasks = []
                            for doc in task_docs:
                                task_info = {
                                    "task_id": str(doc["_id"]),  # This will be like 68d24444a326feeaec46f9aa
                                    "title": doc.get("title", ""),
                                    "status": doc.get("status", "unknown"),
                                    "start_date": str(doc.get("start_date", "")),
                                    "due_date": str(doc.get("due_date", "")),
                                    "description": str(doc.get("description", ""))
                                }
                                site_tasks.append(task_info)
                            
                            enhanced_tasks[site_id] = site_tasks
                            logger.info(f"Found {len(site_tasks)} actual tasks for site {site_id}")
                            
                            # If no tasks found for this site, raise an error
                            if len(site_tasks) == 0:
                                logger.error(f"No tasks found for site {site_id} - cannot update tasks")
                                raise ValueError(f"No tasks found for site {site_id}. Site may not exist or has no tasks.")
                            
                            # DEBUG: Log first task
                            if site_tasks:
                                logger.info(f"DEBUG - First task for site {site_id}: {site_tasks[0]}")
                            
                        except Exception as e:
                            logger.error(f"Error fetching tasks for site {site_id}: {e}")
                            enhanced_tasks[site_id] = []
        
        # Use enhanced tasks if available, otherwise fall back to original
        available_tasks = enhanced_tasks if enhanced_tasks else context.get("iwps", {})
        
        # DEBUG: Log what we're sending to LLM
        logger.info(f"DEBUG - Available tasks structure: {type(available_tasks)}")
        if enhanced_tasks:
            for site_id, tasks in enhanced_tasks.items():
                logger.info(f"DEBUG - Site {site_id}: {len(tasks)} tasks")
        
        prompt = task_prompt_template.format(
            new_messages=json.dumps(context.get("new_messages", []), indent=2),
            available_tasks=json.dumps(available_tasks, indent=2, default=str),
            reasoning=context.get("reasoning", ""),
            From=context.get("From", ""),
            format_instructions=task_parser.get_format_instructions()
        )
        
        response = llm.invoke([HumanMessage(content=prompt)])
        result = task_parser.parse(response.content)
        
        # DEBUG: Show what task_id was extracted
        if result.tasks:
            for task in result.tasks:
                logger.info(f"DEBUG - LLM extracted task_id: '{task.task_id}'")
                logger.info(f"DEBUG - Expected format like: 68d24444a326feeaec46f9aa")
        else:
            logger.warning("DEBUG - No tasks extracted by LLM")
        
        logger.info(f"Successfully parsed task parameters: {len(result.tasks)} tasks")
        return result
        
    except Exception as e:
        logger.error(f"Error in extract_task_parameters: {str(e)}", exc_info=True)
        from agent.schemas import UpdateTaskInput, Task
        return UpdateTaskInput(
            tasks=[],
            title="Error extracting task parameters",
            summary=f"Failed to extract task parameters: {str(e)}",
            reasoning=f"Error: {str(e)}"
        )


def extract_risk_update_parameters(context: Dict[str, Any], llm, db) -> UpdateRiskInput:
    """Extract risk update parameters using LLM."""
    try:
        site_ids = context.get("site_ids", [])
        risks = []
        
        if db is not None and site_ids:
            for site_id in site_ids:
                if site_id is not None:
                    try:
                        site_risks = get_risks_by_site(db, site_id)
                        risks.extend(site_risks)
                    except Exception as e:
                        logger.error(f"Failed to fetch risks for site {site_id}: {str(e)}")
                        continue
        
        concise_risks = []
        for r in risks:
            concise_risks.append({
                "risk_id": str(r.get("_id", "")),
                "title": r.get("title", ""),
                "site_id": str(r.get("site_id", ""))
            })
        
        prompt = risk_update_prompt_template.format(
            new_messages=json.dumps(context.get("new_messages", []), indent=2),
            available_tasks_with_risks=json.dumps(concise_risks, indent=2),
            reasoning=context.get("reasoning", ""),
            From=context.get("From", ""),
            format_instructions=risk_update_parser.get_format_instructions()
        )
        
        response = llm.invoke([HumanMessage(content=prompt)])
        result = risk_update_parser.parse(response.content)
        logger.info(f"Successfully parsed risk update parameters: {len(result.risks)} risks")
        return result
        
    except Exception as e:
        logger.error(f"Error in extract_risk_update_parameters: {str(e)}", exc_info=True)
        from agent.schemas import UpdateRiskInput, RiskUpdate
        return UpdateRiskInput(
            risks=[],
            title="Error extracting risk update parameters",
            summary=f"Failed to extract risk update parameters: {str(e)}",
            reasoning=f"Error: {str(e)}"
        )

def iwp_detection_and_conflict_node(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Updated LangGraph node for IWP detection with boolean classification.
    """
    logger.info("🔍 [IWP_NODE] Starting IWP Detection & Conflict Analysis Node...")
    
    # Extract message content
    whatsapp_messages = state.get("whatsapp_messages", [])
    if whatsapp_messages:
        texts = []
        for m in whatsapp_messages:
            t = m.get("text") or m.get("message") or ""
            if isinstance(t, dict):
                t = t.get("body", "")
            if t:
                texts.append(t)
        message_content = "\n".join(texts).strip()
    else:
        message_content = state.get("email_content", "")
    
    if not message_content:
        error_msg = "❌ No message content found"
        logger.error(error_msg)
        state["iwp_error"] = error_msg
        return state
    
    db = state["db"]
    llm = config.get("llm")
    site_ids = state.get("site_ids", [])
    site_names = state.get("site_names", [])
    
    if not site_ids or not site_names:
        error_msg = "❌ No site information available"
        logger.error(error_msg)
        state["iwp_error"] = error_msg
        return state
    
    try:
        # Step 1: Fetch ALL IWPs for the site(s)
        from .utils import get_full_iwps_with_reasoning
        
        all_iwps_dict = get_full_iwps_with_reasoning(db, site_ids, site_names)
        
        # Flatten to get all IWP documents
        all_iwps = []
        for site_name, iwp_list in all_iwps_dict.items():
            if iwp_list:
                all_iwps.extend(iwp_list)
        
        if not all_iwps:
            logger.warning("No IWPs found for sites")
            state["iwp_detection_result"] = IWPDetectionResult(
                classifications=[],
                related_iwp_ids=[],
                message_summary="No IWPs available for classification"
            )
            return state
        
        logger.info(f"📋 Found {len(all_iwps)} total IWPs to classify")
        
        # Step 2: Detect relationships for ALL IWPs
        detection_result = detect_iwp_relationships(
            message_content=message_content,
            all_iwps=all_iwps,
            llm=llm

        )
        
        state["iwp_detection_result"] = detection_result
        
        # Log results
        logger.info(f"📊 Detection Summary:")
        logger.info(f"  - Total IWPs: {len(detection_result.classifications)}")
        logger.info(f"  - Related IWPs: {len(detection_result.related_iwp_ids)}")
        logger.info(f"  - Related IDs: {detection_result.related_iwp_ids}")
        
        s
        # Step 3: Process conflicts ONLY for related IWPs
        if detection_result.related_iwp_ids:
            import asyncio
            import concurrent.futures
            
            def run_async(coro):
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            
            conflict_results = run_async(
                process_related_iwps_for_conflicts(
                    detection_result=detection_result,
                    message_content=message_content,
                    all_iwps=all_iwps,
                    db=db,
                    llm=llm
                )
            )
            
            state["iwp_conflict_analysis_results"] = conflict_results
        else:
            logger.info("No related IWPs, skipping conflict analysis")
            state["iwp_conflict_analysis_results"] = []
        
        logger.info("✅ [IWP_NODE] IWP Detection & Conflict Analysis Complete!")
        return state
        
    except Exception as e:
        error_msg = f"[IWP_NODE] Error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        state["iwp_error"] = error_msg
        return state