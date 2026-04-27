"""
LangGraph agents for CC-SDD class diagram generation and traceability.

Pipeline:
  1. Creator Agent  — generates a SystemSpec JSON from requirements.md
  2. Reviewer Agent — validates quality, coverage, and provides feedback
  3. Loop up to 3 iterations until APPROVED

Sync pipeline:
  - Diff Agent — detects changes between old and new diagrams
  - Update Agent — patches requirements.md to reflect design changes
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# SystemSpec JSON Schema (matches BESSER frontend ClassDiagramConverter input)
# ---------------------------------------------------------------------------

SYSTEM_SPEC_SCHEMA = """\
{
  "systemName": "string — name of the system",
  "classes": [
    {
      "className": "string — PascalCase class name",
      "attributes": [
        {
          "name": "string — camelCase attribute name",
          "type": "string — e.g. str, int, float, bool, Date, list, or another class name",
          "visibility": "public | private | protected"
        }
      ],
      "methods": [
        {
          "name": "string — camelCase method name",
          "returnType": "string — e.g. void, bool, str, list",
          "visibility": "public | private | protected",
          "parameters": [
            { "name": "string", "type": "string" }
          ]
        }
      ]
    }
  ],
  "relationships": [
    {
      "type": "Association | Inheritance | Composition | Aggregation",
      "sourceClass": "string — must match a className above",
      "targetClass": "string — must match a className above",
      "sourceMultiplicity": "string — e.g. 1, *, 0..1, 1..*",
      "targetMultiplicity": "string — e.g. 1, *, 0..1, 1..*",
      "name": "string — optional relationship label"
    }
  ]
}
"""


# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------

class DiagramState(TypedDict):
    requirements: str
    feature_name: str
    diagram_json: Optional[str]
    feedback: str
    iterations: int
    approved: bool


class SyncState(TypedDict):
    feature_name: str
    old_diagram: str
    new_diagram: str
    requirements: str
    diff_report: str
    updated_requirements: str


# ---------------------------------------------------------------------------
# LLM Factory
# ---------------------------------------------------------------------------

def _get_llm(temperature: float = 0.2):
    api_key = os.environ.get("GEMINI_API_KEY", "")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set. "
            "Set it before starting gemini_service."
        )
    return ChatGoogleGenerativeAI(
        model=model,
        temperature=temperature,
        google_api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Creator Node
# ---------------------------------------------------------------------------

CREATOR_SYSTEM = f"""\
You are an expert UML Class Diagram architect.

Your task is to produce a JSON object conforming EXACTLY to this schema:

{SYSTEM_SPEC_SCHEMA}

Rules:
- Every class MUST have at least one attribute.
- Use PascalCase for class names, camelCase for attributes and methods.
- Types must be primitive (str, int, float, bool, Date) or reference another class name.
- Every relationship source/target must match an existing className.
- Cover ALL functional requirements. Map each requirement to at least one class.
- Do NOT include markdown fences or explanatory text — output ONLY the JSON.
- Inheritance should only be used when there is a clear IS-A relationship.
- Prefer Composition for strong ownership and Aggregation for weak ownership.
- Association is for general references between classes.
"""

CREATOR_WITH_FEEDBACK = """\
Your previous diagram received the following feedback from the reviewer:

--- FEEDBACK ---
{feedback}
--- END FEEDBACK ---

Previous diagram:
```json
{prev_diagram}
```

Fix ALL issues mentioned in the feedback. Output ONLY the corrected JSON.
"""


def creator_node(state: DiagramState) -> dict:
    """Generate or refine the SystemSpec JSON."""
    iteration = state["iterations"] + 1
    print(f"[AGENTS] Creator — iteration {iteration}", flush=True)

    llm = _get_llm(temperature=0.15)

    user_content = f"Requirements:\n\n{state['requirements']}\n\nFeature: {state['feature_name']}"

    if state["feedback"] and state["diagram_json"]:
        user_content += "\n\n" + CREATOR_WITH_FEEDBACK.format(
            feedback=state["feedback"],
            prev_diagram=state["diagram_json"],
        )

    messages = [
        SystemMessage(content=CREATOR_SYSTEM),
        HumanMessage(content=user_content),
    ]

    response = llm.invoke(messages)
    raw = response.content.strip()

    # Strip markdown fences if the LLM wraps the JSON
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return {
        "diagram_json": raw,
        "iterations": iteration,
    }


# ---------------------------------------------------------------------------
# Reviewer Node
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM = """\
You are a senior software architect reviewing a UML Class Diagram JSON for:

1. **Requirements coverage** — every requirement must map to at least one class.
2. **Structural correctness** — no dangling relationships, no empty classes.
3. **Naming conventions** — PascalCase classes, camelCase members.
4. **Relationship quality** — correct types and multiplicities.
5. **JSON validity** — must parse as valid JSON.

If the diagram is acceptable, respond with EXACTLY: APPROVED

If there are issues, provide a numbered list of specific fixes.
Do NOT output a corrected JSON — only the feedback.
"""


def reviewer_node(state: DiagramState) -> dict:
    """Review the diagram and either approve or provide feedback."""
    print("[AGENTS] Reviewer", flush=True)

    llm = _get_llm(temperature=0.1)

    user_content = (
        f"Requirements:\n{state['requirements']}\n\n"
        f"Diagram JSON:\n```json\n{state['diagram_json']}\n```\n\n"
        "Review the diagram and respond with APPROVED or specific feedback."
    )

    messages = [
        SystemMessage(content=REVIEWER_SYSTEM),
        HumanMessage(content=user_content),
    ]

    response = llm.invoke(messages)
    feedback = response.content.strip()
    approved = feedback.upper().startswith("APPROVED")

    print(
        f"[AGENTS] Reviewer verdict: {'APPROVED' if approved else feedback[:120]}...",
        flush=True,
    )

    return {
        "feedback": feedback,
        "approved": approved,
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def router(state: DiagramState) -> str:
    if state["approved"] or state["iterations"] >= 3:
        return END
    return "creator"


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def run_diagram_pipeline(
    feature_name: str,
    requirements_text: str,
) -> Dict[str, Any]:
    """
    Run the Creator→Reviewer loop and return a parsed SystemSpec dict.

    Raises RuntimeError if the final output is not valid JSON.
    """
    print(
        f"\n{'='*60}\n"
        f"[AGENTS] Starting diagram pipeline for '{feature_name}'\n"
        f"{'='*60}",
        flush=True,
    )

    workflow = StateGraph(DiagramState)
    workflow.add_node("creator", creator_node)
    workflow.add_node("reviewer", reviewer_node)

    workflow.set_entry_point("creator")
    workflow.add_edge("creator", "reviewer")
    workflow.add_conditional_edges("reviewer", router)

    app = workflow.compile()

    initial: DiagramState = {
        "requirements": requirements_text,
        "feature_name": feature_name,
        "diagram_json": None,
        "feedback": "",
        "iterations": 0,
        "approved": False,
    }

    final_state = app.invoke(initial)

    raw_json = final_state["diagram_json"]
    iterations = final_state["iterations"]
    approved = final_state["approved"]

    print(
        f"[AGENTS] Pipeline done — {iterations} iterations, "
        f"approved={approved}",
        flush=True,
    )

    # Parse and validate
    try:
        spec = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Creator produced invalid JSON: {exc}\n{raw_json[:500]}")

    # Basic structural validation
    if not isinstance(spec.get("classes"), list) or len(spec["classes"]) == 0:
        raise RuntimeError("Creator produced a SystemSpec with no classes")

    return spec


# ---------------------------------------------------------------------------
# Sync / Traceability Pipeline
# ---------------------------------------------------------------------------

DIFF_SYSTEM = """\
You are a diff analyzer. Compare the OLD class diagram JSON to the NEW one.
List ONLY the differences as a concise numbered list:
- Classes added / removed
- Attributes added / removed / type-changed
- Methods added / removed
- Relationships added / removed / changed

If there are NO differences, respond with EXACTLY: NO_CHANGES
"""

SYNC_SYSTEM = """\
You are a requirements engineer maintaining traceability between a class
diagram and a requirements document.

Given the DIFF between the old and new diagrams, update the requirements
document to reflect the design changes. Rules:
- If new classes were added, add or extend requirements to cover them.
- If classes were removed, mark corresponding requirements as deprecated
  or remove them if they are no longer relevant.
- If attributes/methods changed, adjust acceptance criteria accordingly.
- Preserve the existing document structure (headings, numbering).
- Use the same language as the original requirements (detect from content).
- Output the COMPLETE updated requirements.md content.
"""


def run_sync_pipeline(
    feature_name: str,
    old_diagram: Dict[str, Any],
    new_diagram: Dict[str, Any],
    requirements_text: str,
) -> Optional[str]:
    """
    Compare old and new diagrams. If changes exist, update requirements.
    Returns updated requirements text, or None if no changes detected.
    """
    print(f"[SYNC] Starting traceability sync for '{feature_name}'", flush=True)

    llm = _get_llm(temperature=0.1)

    # Step 1 — Diff
    diff_messages = [
        SystemMessage(content=DIFF_SYSTEM),
        HumanMessage(
            content=(
                f"OLD diagram:\n```json\n{json.dumps(old_diagram, indent=2)}\n```\n\n"
                f"NEW diagram:\n```json\n{json.dumps(new_diagram, indent=2)}\n```"
            )
        ),
    ]
    diff_response = llm.invoke(diff_messages)
    diff_report = diff_response.content.strip()

    if "NO_CHANGES" in diff_report.upper():
        print("[SYNC] No changes detected", flush=True)
        return None

    print(f"[SYNC] Changes detected:\n{diff_report[:300]}", flush=True)

    # Step 2 — Update requirements
    sync_messages = [
        SystemMessage(content=SYNC_SYSTEM),
        HumanMessage(
            content=(
                f"DIFF REPORT:\n{diff_report}\n\n"
                f"CURRENT requirements.md:\n```markdown\n{requirements_text}\n```\n\n"
                f"Output the complete updated requirements.md content."
            )
        ),
    ]
    sync_response = llm.invoke(sync_messages)
    updated = sync_response.content.strip()

    # Strip markdown fences
    updated = re.sub(r"^```(?:markdown)?\s*", "", updated)
    updated = re.sub(r"\s*```$", "", updated)

    print(f"[SYNC] Requirements updated ({len(updated)} chars)", flush=True)
    return updated
