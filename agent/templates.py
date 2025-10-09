
# Context extraction template
context_prompt_template = """
You are an intelligent agent for infrastructure project management via WhatsApp.

**ANALYZE ALL MESSAGES and return a SINGLE JSON response with arrays for all messages.**

### NEW MESSAGES:
{messages}

### Available sites: {sites}

### INSTRUCTIONS:

1. MESSAGE RELEVANCE:
   - A message is RELEVANT if it contains ANY of these:
     * Site names or locations (even partial matches)
     * Task/action words (delay, issue, problem, complete, done, update, etc.)
     * Project-related terms (survey, equipment, team, etc.)
   - Only mark as IRRELEVANT if the message is purely social/greeting with no project context

2. SITE MATCHING (be flexible):
   - Match partial site names (e.g., "pimpri" → "Pimpri Nipani Solar Site")
   - Ignore case differences (e.g., "Pimpri" = "pimpri")
   - Match substrings (e.g., "nipani" → "Pimpri Nipani Solar Site")
   - If multiple sites match, choose the most likely one
   - Extract site_id from the available sites list

3. ACTION DETECTION (in priority order):
   - `add_risk`: Any issues, delays, problems, incidents
   - `update_task`: Task progress, completions, updates
   - `update_risk`: Risk updates, mitigations
   - `update`: General project updates
   - `irrelevant`: ONLY for purely social/greeting messages

4. OUTPUT REQUIREMENTS:
   - Return ONE JSON object with arrays
   - Each array should have one entry per message
   - If no site is mentioned, use null for site_ids and site_names
   - All arrays must have the same length

### OUTPUT FORMAT:
{format_instructions}

### EXAMPLES:

For messages: ["Hello team", "Pimpri site has delay", "Survey done at nipani"]

Expected output:
{{
  "actions": ["irrelevant", "add_risk", "update_task"],
  "site_ids": [null, "68e3a39f419da003605f82f8", "68e3a39f419da003605f82f8"],
  "site_names": [null, "Pimpri Nipani Solar Site", "Pimpri Nipani Solar Site"],
  "chat_id": ["120363421501362287@g.us_", "120363421501362287@g.us_", "120363421501362287@g.us_"],
  "From": "244207612629041@lid",
  "confidence": 0.9,
  "reasoning": "First message is greeting (irrelevant), second mentions delay at pimpri (add_risk), third mentions survey completion at nipani (update_task)"
}}
"""
# Task detection template (detect Task IDs mentioned in WhatsApp messages)
task_detection_prompt_template = """
You are a task detection agent. Your job is to read the WhatsApp message content and identify which Task IDs are referenced, if any.

WhatsApp Message Content:
{email_content_str}

Known Tasks (per site):
{tasks}

Instructions:
- Return a list of task_id values you are confident are referenced.
- Include a confidence score per detected task between 0.0 and 1.0.
- Provide a short reasoning per detected task.
- Provide a short message/snippet per detected task.

Output format:
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