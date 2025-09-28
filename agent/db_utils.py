import datetime
from typing import List, Dict, Optional
from bson import ObjectId
import logging
from .schemas import RiskStatus, TaskStatus
logger = logging.getLogger(__name__)

from .config import DATABASE_CONFIG, get_project_id
from .config import get_redis_connection

import json


def add_risk(risks: List[Dict], summary: str, title: str, From: str, db, work_package_data: Dict = None) -> str:
    """Add multiple risks to the database with work package information."""
    logger.info(f"Executing action: add_risk for {len(risks)} risks. Summary: {summary}")
    
    # Debug first risk to understand the data structure
    if risks:
        logger.debug(f"DEBUG - First risk data structure: {risks[0]}")
        logger.debug(f"DEBUG - site_id type: {type(risks[0].get('site_id'))}")
        logger.debug(f"DEBUG - site_id value: {risks[0].get('site_id')}")

    results = []
    
    for i, risk_data in enumerate(risks):
        site_id = risk_data.get("site_id", "")
        description = risk_data.get("description", "")
        severity = risk_data.get("severity", "medium")
        impact = risk_data.get("impact", "medium")
        mitigation_plan = risk_data.get("mitigation_plan", "")
        reasoning = risk_data.get("reasoning", "medium")
        package_id = risk_data.get("package_id")
            
        logger.info(f"Adding risk {i+1}/{len(risks)} for site_id: {site_id}")
        
        try:
            risk_collection = db[DATABASE_CONFIG["risk_db"]]
            notifications_col = db[DATABASE_CONFIG["notifications_db"]]

            # Normalize enums to plain strings for Mongo storage
            if hasattr(severity, "value"):
                severity = severity.value

            entry = {
                "_id": ObjectId(),
                "title": title,
                "description": description,
                "site_id": site_id,  # Now guaranteed to be a string
                "status": "open",
                "severity": severity,
                "impact": impact,
                "mitigation_plan": mitigation_plan,
                "source": "whatsapp",
                "reasoning": reasoning,
                "created_at": datetime.datetime.utcnow(),
                "updated_at": datetime.datetime.utcnow(),
                "updated_by": ObjectId("68ac443d8f7fde8cc31cc0d9"),
                "agent_update":True,
                "project_id": get_project_id(),
            }
            if package_id:
                entry["package_id"] = package_id
            
            # Add work package information if available
            if work_package_data:
                entry["work_package_impact"] = {
                    "affected_cwps": work_package_data.get("affected_cwps", []),
                    "affected_iwps": work_package_data.get("affected_iwps", []),
                    "primary_cwp": work_package_data.get("primary_cwp"),
                    "primary_iwp": work_package_data.get("primary_iwp"),
                    "impact_level": work_package_data.get("impact_level", "low"),
                    "confidence": work_package_data.get("confidence", 0.0),
                    "reasoning": work_package_data.get("reasoning", "")
                }
                logger.info(f"Added work package impact data to risk: {work_package_data.get('impact_level', 'low')} impact")
               
            result = risk_collection.insert_one(entry)

            notification = {
                "_id": ObjectId(),
                "title": title,
                "description": description,
                "created_at": datetime.datetime.utcnow(),
                "risk_id": str(entry["_id"]),
                "type": "risk",
                "seen": False,
                "project_id": DATABASE_CONFIG["project_id"],
            }
            notifications_col.insert_one(notification)

            r = get_redis_connection()
            try:
                r.publish("notifications", json.dumps({
                    "_id": str(notification["_id"]),
                    "title": notification["title"],
                    "description": notification["description"],
                    "type": "risk",
                    "risk_id": str(entry["_id"]),
                    "seen": "unread",
                    "project_id": str(get_project_id()),
                }))
            finally:
                r.close()

            logger.info(f"Risk inserted: _id={result.inserted_id} into collection {DATABASE_CONFIG['risk_db']}")
            
            results.append(f"Risk for site {site_id} added successfully")
                
        except Exception as e:
            error_msg = f"Failed to add risk for site {site_id}: {e}"
            logger.error(error_msg)
            results.append(error_msg)
    
    success_count = len([r for r in results if "successfully" in r])
    total_count = len(risks)
    
    logger.info(f"Batch risk addition completed: {success_count}/{total_count} risks added successfully.")
    if success_count != total_count:
        logger.warning(f"Failed to add {total_count - success_count} risks.")
    logger.debug(f"Details: {'; '.join(results)}")
    
    return f"Batch risk addition completed: {success_count}/{total_count} risks added successfully. Details: {'; '.join(results)}"


def update_task(tasks: List[dict], summary: str, title: str, From: str, db, work_package_data, reasoning: Dict = None) -> str:
    """
    Update multiple tasks in the database with work package information.
    Also logs into communications collection for audit.
    
    Args:
        tasks: List of task dictionaries to update
        summary: Summary of the update
        title: Title of the update
        From: Sender's email
        db: Database connection
        work_package_data: Dictionary containing work package information
        reasoning: Explanation for the task update
    """
    logger.info(f"Executing action: update_task for {len(tasks)} tasks. Summary: {summary}")
    results = []

    for i, task_data in enumerate(tasks):
        task_id = task_data.get("task_id")
        status = task_data.get("status")
        reasoning = task_data.get("reasoning")

        logger.info(f"Updating task {i+1}/{len(tasks)} with task_id: {task_id}")

        try:
            task_collection = db[DATABASE_CONFIG["iwp_db"]]
            comms_collection = db[DATABASE_CONFIG["communications_db"]]
            notif_collection = db[DATABASE_CONFIG["notifications_db"]]
            
            # Log the task ID and collection being used
            logger.info(f"Attempting to update task with ID: {task_id}")
            logger.info(f"Using collection: {DATABASE_CONFIG['iwp_db']}")

            # Build update fields
            update_fields = {
                "status": status,
                "updated_at": datetime.datetime.utcnow(),
                "updated_by": ObjectId("68ac443d8f7fde8cc31cc0d9"),
                "reasoning": reasoning,
                "agent_update": True
            }

            # Add work package information if available
            if work_package_data:
                update_fields["work_package_impact"] = {
                    "affected_cwps": work_package_data.get("affected_cwps", []),
                    "affected_iwps": work_package_data.get("affected_iwps", []),
                    "primary_cwp": work_package_data.get("primary_cwp"),
                    "primary_iwp": work_package_data.get("primary_iwp"),
                    "impact_level": work_package_data.get("impact_level", "low"),
                    "confidence": work_package_data.get("confidence", 0.0),
                    "reasoning": work_package_data.get("reasoning", "")
                }
                logger.info(f"Added work package impact data to task: {work_package_data.get('impact_level', 'low')} impact")

            # If task is completed or closed, mark completed_at
            if status.upper() in ["COMPLETED", "CLOSED"]:
                update_fields["completed_at"] = datetime.datetime.utcnow()

            # Try different query formats
            filter_queries = [
                {"_id": ObjectId(task_id)},
                {"task_id": task_id},
                {"task_number": task_id}
            ]
            
            if not ObjectId.is_valid(task_id):
                # If it's not a valid ObjectId, try it as a direct string
                filter_queries.insert(1, {"_id": task_id})
            
            updated = False
            last_error = None
            
            for query in filter_queries:
                try:
                    # Log the query being attempted
                    logger.info(f"Attempting query: {query}")
                    
                    # First check if the document exists
                    existing = task_collection.find_one(query, {"_id": 1})
                    if existing:
                        logger.info(f"Found task with query {query}: {existing}")
                        result = task_collection.update_one(query, {"$set": update_fields})
                        if result.modified_count > 0:
                            results.append(f"Task {task_id} updated successfully using query {query}")
                            updated = True
                            break
                        else:
                            last_error = f"Task {task_id} found but not updated (no changes?)"
                            logger.warning(last_error)
                    else:
                        last_error = f"Task {task_id} not found with query {query}"
                        logger.warning(last_error)
                except Exception as e:
                    last_error = f"Error querying with {query}: {str(e)}"
                    logger.error(last_error, exc_info=True)
            
            if not updated:
                results.append(f"Failed to update task {task_id}. Last error: {last_error}")
                # Log the first few documents in the collection for debugging
                try:
                    sample_docs = list(task_collection.find({}, {"_id": 1, "task_id": 1, "task_number": 1}).limit(5))
                    logger.info(f"Sample documents in {DATABASE_CONFIG['iwp_db']}: {sample_docs}")
                except Exception as e:
                    logger.error(f"Error sampling collection: {str(e)}")

            # Log into communications for audit
            comms_doc = {
            "_id": ObjectId(),
            "type": "update",
            "message": summary,
            "title": title,
            "status": "open", 
            "source": "whatsapp",
            "action_taken": "decision", 
            "From": From,
            "created_at": datetime.datetime.utcnow(),
            "updated_at": datetime.datetime.utcnow(),
            "acknowledged_at": datetime.datetime.utcnow(),
            "project_id": DATABASE_CONFIG["project_id"],
            }
            
            # Add work package information to communication log
            if work_package_data:
                comms_doc["work_package_impact"] = {
                    "affected_cwps": work_package_data.get("affected_cwps", []),
                    "affected_iwps": work_package_data.get("affected_iwps", []),
                    "primary_cwp": work_package_data.get("primary_cwp"),
                    "primary_iwp": work_package_data.get("primary_iwp"),
                    "impact_level": work_package_data.get("impact_level", "low"),
                    "confidence": work_package_data.get("confidence", 0.0),
                    "reasoning": work_package_data.get("reasoning", "")
                }
            
            comms_collection.insert_one(comms_doc)

            notification = {
                "title": title,
                "description": summary,
                "comms_id": str(comms_doc["_id"]),
                "created_at": datetime.datetime.utcnow(),
                "seen": False,
                "type": "update",
                "project_id": DATABASE_CONFIG["project_id"],
            }
            
            notif_result = db[DATABASE_CONFIG["notifications_db"]].insert_one(notification)
            r = get_redis_connection()
            try:
                r.publish("notifications", json.dumps({
                    "_id": str(notif_result.inserted_id),
                    "title": notification["title"],
                    "description": notification["description"],
                    "type": "update",
                    "comms_id": str(comms_doc["_id"]),
                    "seen": "unread",
                    "project_id": str(get_project_id()),
                }))
            finally:
                r.close()
        except Exception as e:
            error_msg = f"Failed to update task {task_id}: {e}"
            logger.error(error_msg)
            results.append(error_msg)

    success_count = len([r for r in results if "successfully" in r])
    total_count = len(tasks)

    logger.info(f"Batch task update completed: {success_count}/{total_count} tasks updated successfully.")
    if success_count != total_count:
        logger.warning(f"Failed to update {total_count - success_count} tasks.")
    logger.debug(f"Details: {'; '.join(results)}")

    return f"Batch task update completed: {success_count}/{total_count} tasks updated. Details: {'; '.join(results)}"


def update_risk(risks: List[dict], summary: str, title: str, From: str, db, work_package_data: Dict = None) -> str:
    """
    Update multiple risks in the database with work package information.
    Also logs into communications collection.
    """
    logger.info(f"Executing action: update_risk for {len(risks)} risks. Summary: {summary}")
    results = []

    for i, risk_data in enumerate(risks):
        risk_id = risk_data.get("risk_id")
        # Handle case where risk_id might be a list
        if isinstance(risk_id, list):
            if risk_id:  # If list is not empty, take the first element
                risk_id = risk_id[0]
                logger.warning(f"Risk ID was a list, using first element: {risk_id}")
            else:  # If list is empty, skip this risk
                results.append("Skipping risk with empty risk_id list")
                continue
                
        status = risk_data.get("status")
        mitigation_plan = risk_data.get("mitigation_plan", "")
        reasoning = risk_data.get("reasoning", "")
        package_id = risk_data.get("package_id")

        logger.info(f"Updating risk {i+1}/{len(risks)} with risk_id: {risk_id}")

        try:
            if not risk_id:
                results.append("Skipping risk with no risk_id")
                continue
                
            risk_collection = db[DATABASE_CONFIG["risk_db"]]
            comms_collection = db[DATABASE_CONFIG["communications_db"]]

            # Normalize enum to plain string for Mongo storage
            status_val = status.value if hasattr(status, "value") else status

            # build update document
            update_fields = {
                "status": status_val,
                "mitigation_plan": mitigation_plan,
                "reasoning": reasoning,
                "updated_at": datetime.datetime.utcnow(),
                "updated_by": ObjectId("68ac443d8f7fde8cc31cc0d9"),
                "agent_update":True
            }
            if package_id:
                update_fields["package_id"] = package_id

            # Add work package information if available
            if work_package_data:
                update_fields["work_package_impact"] = {
                    "affected_cwps": work_package_data.get("affected_cwps", []),
                    "affected_iwps": work_package_data.get("affected_iwps", []),
                    "primary_cwp": work_package_data.get("primary_cwp"),
                    "primary_iwp": work_package_data.get("primary_iwp"),
                    "impact_level": work_package_data.get("impact_level", "low"),
                    "confidence": work_package_data.get("confidence", 0.0),
                    "reasoning": work_package_data.get("reasoning", "")
                }
                logger.info(f"Added work package impact data to risk update: {work_package_data.get('impact_level', 'low')} impact")

            # set timestamps based on status (compare to Enum values)
            if status_val == RiskStatus.MITIGATED.value:
                update_fields["mitigated_at"] = datetime.datetime.utcnow()
            elif status_val in [RiskStatus.RESOLVED.value, RiskStatus.CLOSED.value]:
                update_fields["resolved_at"] = datetime.datetime.utcnow()

            filter_query = {"$or": [
                {"_id": ObjectId(risk_id)},
                {"risk_id": risk_id}
            ]} if ObjectId.is_valid(risk_id) else {"risk_id": risk_id}

            result = risk_collection.update_one(filter_query, {"$set": update_fields})

            if result.modified_count > 0:
                results.append(f"Risk {risk_id} updated successfully")
            else:
                results.append(f"Risk {risk_id} not found or no changes applied")

            # log into comms for traceability
            comms_doc = {
            "_id": ObjectId(),
            "type": "update",
            "message": summary,
            "title": title,
            "status": "open", 
            "source": "email",
            "action_taken": "decision", 
            "From": From,
            "package_id": package_id,
            "created_at": datetime.datetime.utcnow(),
            "updated_at": datetime.datetime.utcnow(),
            "acknowledged_at": datetime.datetime.utcnow(),
            "project_id": DATABASE_CONFIG["project_id"],  
            }
            
            # Add work package information to communication log
            if work_package_data:
                comms_doc["work_package_impact"] = {
                    "affected_cwps": work_package_data.get("affected_cwps", []),
                    "affected_iwps": work_package_data.get("affected_iwps", []),
                    "primary_cwp": work_package_data.get("primary_cwp"),
                    "primary_iwp": work_package_data.get("primary_iwp"),
                    "impact_level": work_package_data.get("impact_level", "low"),
                    "confidence": work_package_data.get("confidence", 0.0),
                    "reasoning": work_package_data.get("reasoning", "")
                }
            
            comms_collection.insert_one(comms_doc)

            notification = {
                "title": title,
                "description": summary,
                "comms_id": str(comms_doc["_id"]),
                "risk_id": str(risk_id),
                "type": "risk",
                "created_at": datetime.datetime.utcnow(),
                "seen": False,
                "project_id": DATABASE_CONFIG["project_id"],
            }

            notif_result = db[DATABASE_CONFIG["notifications_db"]].insert_one(notification)
            r = get_redis_connection()

            try:
                
                r.publish("notifications", json.dumps({
                    "_id": str(notif_result.inserted_id),
                    "title": notification["title"],
                    "description": notification["description"],
                    "type": "risk",
                    "comms_id": str(comms_doc["_id"]),
                    "risk_id": str(risk_id),
                    "seen": "unread",
                    "project_id": str(DATABASE_CONFIG["project_id"]),
                }))
            finally:
                r.close()
            logger.info(f"Risk {risk_id} updated successfully")

        except Exception as e:
            error_msg = f"Failed to update risk {risk_id}: {e}"
            logger.error(error_msg)
            results.append(error_msg)

    success_count = len([r for r in results if "successfully" in r])
    total_count = len(risks)

    logger.info(f"Batch risk update completed: {success_count}/{total_count} risks updated successfully.")
    if success_count != total_count:
        logger.warning(f"Failed to update {total_count - success_count} risks.")
    logger.debug(f"Details: {'; '.join(results)}")

    return f"Batch risk update completed: {success_count}/{total_count} risks updated. Details: {'; '.join(results)}"


def update_response_in_db(
    type: str,
    message: str,
    title: str,
    status: str,
    action_taken: str,
    From: str,
    db,
    work_package_data: Dict = None
) -> dict:
    """
    Update a response document in MongoDB with work package information.

    Args:
        db (Database): MongoDB database object
        type (str): Response type
        message (str): Response message
        title (str): Response title
        status (str): Response status
        action_taken (str): Action taken
        From (str): Email sender
        work_package_data (Dict): Work package classification data
    Returns:
        dict: Update result
    """
    try:
        collection = db[DATABASE_CONFIG["communications_db"]]
        update_doc = {
            "type": type,
            "message": message,
            "title": title,
            "status": status,
            "action_taken": action_taken,
            "source": "whatsapp",
            "From": From, 
            "created_at": datetime.datetime.utcnow(),
            "updated_at": datetime.datetime.utcnow(),
            "acknowledged_at": datetime.datetime.utcnow(),
            "project_id": DATABASE_CONFIG["project_id"],
            "updated_by": ObjectId("68ac443d8f7fde8cc31cc0d9"),
            "agent_update":True
        }
        
        # Add work package information if available
        if work_package_data:
            update_doc["work_package_impact"] = {
                "affected_cwps": work_package_data.get("affected_cwps", []),
                "affected_iwps": work_package_data.get("affected_iwps", []),
                "primary_cwp": work_package_data.get("primary_cwp"),
                "primary_iwp": work_package_data.get("primary_iwp"),
                "impact_level": work_package_data.get("impact_level", "low"),
                "confidence": work_package_data.get("confidence", 0.0),
                "reasoning": work_package_data.get("reasoning", "")
            }
            logger.info(f"Added work package impact data to response: {work_package_data.get('impact_level', 'low')} impact")

        collection.insert_one(update_doc)

        # Skip creating notification if action is ask_clarification
        if action_taken != "irrelevant":
            notification = {
                "title": title,
                "description": message,
                "comms_id": str(update_doc["_id"]),
                "created_at": datetime.datetime.utcnow(),
                "seen": False,
                "type": type,
                "project_id": get_project_id(),
            }
            
            notification_result = db[DATABASE_CONFIG["notifications_db"]].insert_one(notification)

            r = get_redis_connection()
            try:
                r.publish("notifications", json.dumps({
                    "_id": str(notification_result.inserted_id),
                    "title": notification["title"],
                    "description": notification["description"],
                    "comms_id": str(update_doc["_id"]),
                    "seen": "unread",
                    "type": type,
                    "project_id": str(get_project_id()),
                }))
            finally:
                r.close()
            logger.info(f"Notification created for response: {update_doc}")
        else:
            logger.info("Skipping notification creation for ask_clarification action")
    except Exception as e:
        logger.error(f"Failed to update response: {e}")

    return {
        "updated_fields": update_doc
    }


def create_communication_log(
    communication_type: str,
    message: str,
    title: str,
    status: str,
    From: str,
    db,
    work_package_data: Dict = None,
    additional_data: Dict = None
) -> str:
    """
    Create a general communication log entry with work package information.
    
    Args:
        communication_type (str): Type of communication (email, update, notification, etc.)
        message (str): Communication message content
        title (str): Communication title
        status (str): Communication status
        From (str): Sender information
        db: Database connection
        work_package_data (Dict): Work package classification data
        additional_data (Dict): Any additional data to store
        project_id (str): Project ID
    Returns:
        str: Result message
    """
    try:
        comms_collection = db[DATABASE_CONFIG["communications_db"]]
        
        communication_doc = {
            "_id": ObjectId(),
            "type": communication_type,
            "message": message,
            "title": title,
            "status": status,
            "source": "whatsapp",
            "From": From,
            "created_at": datetime.datetime.utcnow(),
            "updated_at": datetime.datetime.utcnow(),
            "acknowledged_at": datetime.datetime.utcnow()
        }
        
        # Add work package information if available
        if work_package_data:
            communication_doc["work_package_impact"] = {
                "affected_cwps": work_package_data.get("affected_cwps", []),
                "affected_iwps": work_package_data.get("affected_iwps", []),
                "primary_cwp": work_package_data.get("primary_cwp"),
                "primary_iwp": work_package_data.get("primary_iwp"),
                "impact_level": work_package_data.get("impact_level", "low"),
                "confidence": work_package_data.get("confidence", 0.0),
                "reasoning": work_package_data.get("reasoning", "")
            }
        
        # Add any additional data
        if additional_data:
            communication_doc.update(additional_data)
        
        result = comms_collection.insert_one(communication_doc)
        
        # Create notification
        notification = {
            "title": title,
            "description": message,
            "comms_id": str(communication_doc["_id"]),
            "created_at": datetime.datetime.utcnow(),
            "seen": False,
            "type": communication_type
        }
        
        notification_result = db[DATABASE_CONFIG["notifications_db"]].insert_one(notification)

        r = get_redis_connection()
        try:
            r.publish("notifications", json.dumps({
                "_id": str(notification_result.inserted_id),
                "title": notification["title"],
            "description": notification["description"],
            "comms_id": str(communication_doc["_id"]),
            "seen": "unread",
            "type": communication_type
        }))
        finally:
            r.close()
        
        logger.info(f"Communication log created: {communication_type} - {title}")
        return f"Communication log created successfully: {communication_type}"
        
    except Exception as e:
        error_msg = f"Failed to create communication log: {e}"
        logger.error(error_msg)
        return error_msg


def bulk_update_work_package_impact(
    collection_name: str,
    document_ids: List[str],
    work_package_data: Dict,
    db
) -> str:
    """
    Bulk update work package impact data for multiple documents.
    
    Args:
        collection_name (str): Name of the collection to update
        document_ids (List[str]): List of document IDs to update
        work_package_data (Dict): Work package classification data
        db: Database connection
        
    Returns:
        str: Result message
    """
    try:
        collection = db[collection_name]
        
        work_package_impact = {
            "affected_cwps": work_package_data.get("affected_cwps", []),
            "affected_iwps": work_package_data.get("affected_iwps", []),
            "primary_cwp": work_package_data.get("primary_cwp"),
            "primary_iwp": work_package_data.get("primary_iwp"),
            "impact_level": work_package_data.get("impact_level", "low"),
            "confidence": work_package_data.get("confidence", 0.0),
            "reasoning": work_package_data.get("reasoning", ""),
            "updated_at": datetime.datetime.utcnow()
        }
        
        # Convert string IDs to ObjectIds where valid
        object_ids = []
        for doc_id in document_ids:
            if ObjectId.is_valid(doc_id):
                object_ids.append(ObjectId(doc_id))
            else:
                object_ids.append(doc_id)  # Keep as string if not valid ObjectId
        
        # Bulk update
        result = collection.update_many(
            {"_id": {"$in": object_ids}},
            {"$set": {"work_package_impact": work_package_impact}}
        )
        
        logger.info(f"Bulk work package update: {result.modified_count}/{len(document_ids)} documents updated in {collection_name}")
        return f"Bulk work package update completed: {result.modified_count}/{len(document_ids)} documents updated"
        
    except Exception as e:
        error_msg = f"Failed to bulk update work package impact: {e}"
        logger.error(error_msg)
        return error_msg


def get_work_package_impact_summary(db, collection_name: str = None) -> Dict:
    """
    Get a summary of work package impacts across collections.
    
    Args:
        db: Database connection
        collection_name (str): Specific collection to analyze, or None for all
        
    Returns:
        Dict: Summary of work package impacts
    """
    try:
        collections_to_analyze = [collection_name] if collection_name else [
            DATABASE_CONFIG["risk_db"],
            DATABASE_CONFIG["iwp_db"],
            DATABASE_CONFIG["communications_db"]
        ]
        
        summary = {}
        
        for coll_name in collections_to_analyze:
            collection = db[coll_name]
            
            # Count documents with work package impact data
            total_docs = collection.count_documents({})
            docs_with_wp = collection.count_documents({"work_package_impact": {"$exists": True}})
            
            # Aggregate impact levels
            pipeline = [
                {"$match": {"work_package_impact": {"$exists": True}}},
                {"$group": {
                    "_id": "$work_package_impact.impact_level",
                    "count": {"$sum": 1}
                }}
            ]
            
            impact_levels = list(collection.aggregate(pipeline))
            impact_level_counts = {item["_id"]: item["count"] for item in impact_levels}
            
            # Get most affected CWPs/IWPs
            cwp_pipeline = [
                {"$match": {"work_package_impact.affected_cwps": {"$exists": True, "$ne": []}}},
                {"$unwind": "$work_package_impact.affected_cwps"},
                {"$group": {
                    "_id": "$work_package_impact.affected_cwps",
                    "count": {"$sum": 1}
                }},
                {"$sort": {"count": -1}},
                {"$limit": 10}
            ]
            
            most_affected_cwps = list(collection.aggregate(cwp_pipeline))
            
            summary[coll_name] = {
                "total_documents": total_docs,
                "documents_with_work_package_data": docs_with_wp,
                "coverage_percentage": round((docs_with_wp / total_docs * 100), 2) if total_docs > 0 else 0,
                "impact_level_distribution": impact_level_counts,
                "most_affected_cwps": most_affected_cwps[:5]  # Top 5
            }
        
        logger.info(f"Work package impact summary generated for {len(collections_to_analyze)} collections")
        return summary
        
    except Exception as e:
        logger.error(f"Failed to generate work package impact summary: {e}")
        return {"error": str(e)}