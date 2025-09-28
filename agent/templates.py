
# Context extraction template
context_prompt_template = """
You are an intelligent agent for infrastructure project management via WhatsApp.

**PROCESS EACH NEW MESSAGE INDIVIDUALLY and return ONE action per message.**

### NEW MESSAGES:
{messages}

### Available sites: {sites}

### INSTRUCTIONS:

you will gibven whatsapp messages as an input, if its irrelavnt extract the actions.

1. Extract site ids and action for given messages:
- if the message is relevant and contains the site name, extract the site id and action.
-the site name might not be always exact match, so use fuzzy matching to find the site name.  
- for every action they can perform, extract site ids and action
- thread_id (use chat_id from WhatsApp)
- message_id (use WhatsApp message_id if present, else null)
- site info only if explicitly mentioned

2. Action rules:
   - `add_risk`: problems, delays, incidents — only if site is valid
   - `update_task`: task progress — only if site is valid
   - `update_risk`: mitigation completion — only if site is valid
   - `update`: general updates if site not mentioned or invalid
   - `irrelevant`: casual, greetings, non-project content

### OUTPUT JSON FORMAT:

1. number of actoins and site ids should be same.
2. Use null for missing site info or message_id.
3. From, confidence, reasoning are single values for the batch.
4. IMPORTANT: All arrays (site_ids, site_names, chat_id) must be FLAT LISTS of strings. Do not nest arrays.
   - CORRECT: "site_ids": ["id1", "id2"]
   - INCORRECT: "site_ids": [["id1"], ["id2"]]

{format_instructions}
"""

# Risk addition template
risk_prompt_template = """
Based on the following WhatsApp conversation, extract risk information to add:

WhatsApp Messages: {new_messages}
Available Sites: {available_sites}
Site Names: {site_names}
From: {From}
Available Tasks: {available_tasks}
Available Risks: {available_risks}
Reasoning: {reasoning}

Extract the following for each risk:
- site_id: Which site this risk affects
- title: Brief risk title
- description: Detailed risk description
- severity: high/medium/low
- category: type of risk
- status: open (default)

{format_instructions}
"""

# Task update template
task_prompt_template = """
Based on the following WhatsApp conversation, extract task updates:

WhatsApp Messages: {new_messages}
Available Tasks: {available_tasks}
Reasoning: {reasoning}
From: {From}

Extract the following for each task update:
- task_id: ID of task to update
- status: new status for the task
- notes: any additional notes
- completion_percentage: if mentioned

{format_instructions}
"""

# Risk update template
risk_update_prompt_template = """
Based on the following WhatsApp conversation, extract risk updates:

WhatsApp Messages: {new_messages}
Available Tasks with Risks: {available_tasks_with_risks}
Reasoning: {reasoning}
From: {From}

Extract the following for each risk update:
- risk_id: ID of risk to update
- status: new status (open/mitigated/resolved/closed)
- notes: any additional notes
- severity: updated severity if mentioned

{format_instructions}
"""