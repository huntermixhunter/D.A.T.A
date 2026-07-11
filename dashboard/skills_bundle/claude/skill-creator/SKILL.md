---
name: skill-creator
description: "Build new skills for the DATA system — both instruction-based skill files and hard-coded Python bridge tools."
version: 1.0.0
author: DATA
created_by: agent
platforms: [windows]
metadata:
  hermes:
    tags: [data, skills, tools, bridge, development, self-improvement]
    related_skills: [hermes-agent]
---

# Skill Creator — DATA System

This skill teaches Data how to extend his own capabilities. There are two distinct types of skills with different scopes, complexity, and use cases. Always choose the right type before building.

---

## Choosing the Right Type

| | Type 1: Skill File | Type 2: Bridge Tool |
|---|---|---|
| **What it is** | Markdown instructions Data loads at runtime | Python function wired into the API loop |
| **What it can do** | Guide complex multi-step workflows using existing tools | Adds a brand-new capability (new API, new system call, new hardware) |
| **Requires restart** | No — appears on Skills tab refresh | Yes — bridge must restart to load new code |
| **Complexity** | Write markdown | Write Python + wire into 3 places |
| **Best for** | Research workflows, creative processes, domain procedures, step-by-step recipes | Web scraping a specific API, controlling new hardware, new file formats, anything the current tools cannot do at all |

**Rule of thumb:** If you can accomplish the task using the current tools (terminal, execute_python, web_search, etc.) with good instructions, use Type 1. Only use Type 2 when a native function is genuinely necessary.

---

## Type 1 — Skill Instruction File

### What it does

A `.md` file that Data reads via the `load_skill` tool. It provides detailed workflows, templates, domain knowledge, and step-by-step procedures for complex recurring tasks. Data references it like a manual before executing the task.

### File location

```
C:\Users\mixma\AppData\Local\hermes\skills\<category>\<skill-name>\SKILL.md
```

Categories that already exist: `autonomous-ai-agents`, `creative`, `data-system`

Create a new category folder freely — use lowercase, hyphen-separated names.

### Template

```markdown
---
name: your-skill-name
description: "One sentence describing what this skill does."
version: 1.0.0
author: DATA
created_by: agent
platforms: [windows]
metadata:
  hermes:
    tags: [tag1, tag2, tag3]
---

# Skill Name — Brief Title

One paragraph explaining what this skill is for and when to use it.

---

## When to Use This Skill

- Scenario 1
- Scenario 2

---

## Step-by-Step Workflow

### Step 1 — Do the first thing

Explain what to do and why. Include exact commands, file paths, or API calls.

```python
# Example code if relevant
result = some_function()
```

### Step 2 — Do the next thing

Continue with specifics.

---

## Templates

Include any reusable templates, prompt templates, or file structures here.

---

## Common Issues and Fixes

| Problem | Cause | Fix |
|---------|-------|-----|
| Error message | Root cause | Solution |

---

## Notes and Caveats

Anything Data should know that isn't obvious from the steps.
```

### How to create it

1. Decide on a category name and skill name
2. Write the SKILL.md content — be specific. Vague instructions produce vague results.
3. Save it:
   ```
   write_file(
     path="C:/Users/mixma/AppData/Local/hermes/skills/<category>/<skill-name>/SKILL.md",
     content="<full skill content>"
   )
   ```
4. Verify it exists:
   ```
   list_directory(path="C:/Users/mixma/AppData/Local/hermes/skills/<category>/")
   ```
5. Test loading it:
   ```
   load_skill(skill_name="<skill-name>")
   ```
6. Switch to the Skills tab and click Refresh — it will appear immediately. No restart needed.

### What makes a good skill file

- **Be specific about file paths.** Use exact Windows paths like `C:/Users/mixma/Documents/DATA/`.
- **Include real examples.** Show actual commands, not pseudocode.
- **Anticipate failure.** Include troubleshooting sections for the two or three most likely errors.
- **Keep it scannable.** Data reads this in the middle of a task. Headers and tables beat prose.
- **One skill, one job.** Do not combine unrelated workflows into one file.

---

## Type 2 — Bridge Tool (Python)

### What it does

Adds a new native tool function to `bridge_server.py` — the Python server that connects the dashboard to the Claude API. New tools appear in the API tool list and Data can call them like any other tool (web_search, terminal, etc.).

### Bridge file location

```
C:\Users\mixma\Documents\DATA\lcars-dashboard\bridge_server.py
```

### Three places to edit

Every new tool requires changes in exactly three places in `bridge_server.py`:

**1. The `TOOLS` list** (around line 263) — declares the tool to the Claude API:

```python
{
    "name": "your_tool_name",
    "description": "Clear description of what this tool does. Claude reads this to decide when to use it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "param_one": {
                "type": "string",
                "description": "What this parameter is for"
            },
            "param_two": {
                "type": "integer",
                "description": "What this number means"
            }
        },
        "required": ["param_one"]   # only list truly required params
    }
},
```

**2. The tool executor function** (after line 407, before `TOOL_HANDLERS`) — the actual Python code:

```python
def tool_your_tool_name(param_one: str, param_two: int = 10) -> str:
    """Brief docstring."""
    try:
        # Your implementation here
        result = do_something(param_one, param_two)
        return str(result)
    except Exception as e:
        return f"Error: {e}"
```

Rules for executor functions:
- Always return a string
- Always wrap in try/except — a crash here kills the entire API call
- Use `log.info(f"...")` for debug logging
- Keep imports at the top of the file in the existing import block

**3. The `TOOL_HANDLERS` dict** (around line 657) — maps tool name to the function:

```python
"your_tool_name": lambda inp: tool_your_tool_name(inp["param_one"], inp.get("param_two", 10)),
```

Use `inp["key"]` for required params (raises KeyError if missing — good, fails fast).
Use `inp.get("key", default)` for optional params.

### Full workflow for adding a bridge tool

1. Read the current bridge file:
   ```
   read_file(path="C:/Users/mixma/Documents/DATA/lcars-dashboard/bridge_server.py")
   ```

2. Find the three insertion points by searching for:
   - `"load_skill"` — add your TOOLS entry after this one
   - `def tool_load_skill` — add your function after this one  
   - `"load_skill": lambda` — add your TOOL_HANDLERS entry after this one

3. Write the updated file back with write_file.

4. Tell the Captain: "I have added the tool. The bridge needs to be restarted for it to activate. Please restart using launch_data.bat."

5. After restart, the new tool appears automatically in the Skills tab.

### Importing new Python packages

If the new tool needs a package not already imported, add the import near the top of `bridge_server.py` in the existing import block. If the package is not in the standard library, check if it is installed first:

```
terminal(command="C:/Users/mixma/AppData/Local/Python/bin/python.exe -c \"import package_name; print('ok')\"")
```

If it is not installed:
```
terminal(command="C:/Users/mixma/AppData/Local/Python/bin/python.exe -m pip install package_name")
```

### Current imports already available (no install needed)

`json`, `os`, `re`, `sys`, `time`, `datetime`, `pathlib.Path`, `subprocess`, `urllib`, `socket`, `threading`, `logging`, `tempfile`, `shutil`, `html` (as html_module), `anthropic`, `pyperclip`, `PIL` (Pillow)

---

## Naming Conventions

- Tool function names: `tool_snake_case` → e.g., `tool_send_email`, `tool_get_weather`
- Tool API names (in TOOLS list): `snake_case` → e.g., `send_email`, `get_weather`
- Skill file names: `kebab-case` → e.g., `email-digest`, `weather-monitor`
- Category names: `kebab-case` → e.g., `communication`, `data-system`
- Keep tool names short — they appear in logs and the Skills panel

---

## After Creating Any Skill

1. Use `load_skill(skill_name="skill-name")` to verify it loads correctly
2. Test it on a simple case before using it in a complex workflow
3. Use `remember()` to note what the skill does and when to use it, so future sessions know it exists
4. If it is useful enough to share, the skill file is already in the right format for the Hermes skills hub

---

## Example: Creating a Complete Type 1 Skill

Goal: Create a skill for summarizing YouTube videos.

```
write_file(
  path="C:/Users/mixma/AppData/Local/hermes/skills/media/youtube-summarizer/SKILL.md",
  content="""---
name: youtube-summarizer
description: "Extract and summarize content from YouTube videos using transcript APIs."
version: 1.0.0
author: DATA
created_by: agent
platforms: [windows]
metadata:
  hermes:
    tags: [youtube, video, summary, media]
---

# YouTube Summarizer

Summarize any YouTube video by extracting its transcript and applying structured analysis.

## Workflow

### Step 1 — Get the transcript

Use youtube-transcript-api via execute_python:

\`\`\`python
from youtube_transcript_api import YouTubeTranscriptApi
video_id = "VIDEO_ID_HERE"
transcript = YouTubeTranscriptApi.get_transcript(video_id)
text = " ".join([entry["text"] for entry in transcript])
print(text[:5000])
\`\`\`

### Step 2 — Summarize

With the transcript in hand, produce:
- 3-sentence overview
- Key points as a bullet list
- Notable quotes (if any)
- Recommended follow-up questions

## Notes

- video_id is the part after ?v= in the URL
- Some videos have no transcript — if so, notify the Captain
- Install if needed: pip install youtube-transcript-api
"""
)
```

---

## Example: Adding a Type 2 Tool (get_weather)

Add this to TOOLS list:
```python
{
    "name": "get_weather",
    "description": "Get current weather and forecast for any city.",
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, e.g. 'New York' or 'London'"}
        },
        "required": ["city"]
    }
},
```

Add this function (uses only stdlib — no install needed):
```python
def tool_get_weather(city: str) -> str:
    try:
        encoded = urllib.parse.quote_plus(city)
        url = f"https://wttr.in/{encoded}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "DATA/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        curr = data["current_condition"][0]
        return (
            f"Weather in {city}: {curr['weatherDesc'][0]['value']}, "
            f"{curr['temp_C']}°C / {curr['temp_F']}°F, "
            f"humidity {curr['humidity']}%, "
            f"wind {curr['windspeedKmph']} km/h"
        )
    except Exception as e:
        return f"Weather error: {e}"
```

Add to TOOL_HANDLERS:
```python
"get_weather": lambda inp: tool_get_weather(inp["city"]),
```

Add to TOOL_META in the /skills endpoint:
```python
"get_weather": {"category": "INTELLIGENCE & RESEARCH", "icon": "🌤️", "desc": "Current weather for any city"},
```
