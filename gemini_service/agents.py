"""
LangGraph agents for CC-SDD class diagram generation and traceability.

Pipeline:
  1. Creator Agent            — generates a SystemSpec JSON from requirements.md
  2. Reviewer Agent           — validates quality, structure, and JSON validity
  3. Traceability Reviewer    — validates Requirement↔Class bidirectional mapping
  4. Traceability Generator   — produces traceability.md
  Loop up to 3 iterations until APPROVED by all reviewers.

Sync pipeline:
  - Diff Agent — detects changes between old and new diagrams
  - Update Agent — patches requirements.md to reflect design changes
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict


MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-2.5-pro",
    "gemini-1.5-pro",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
]


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
    traceability_approved: bool
    traceability_feedback: str
    traceability_md: str


class SyncState(TypedDict):
    feature_name: str
    old_diagram: str
    new_diagram: str
    requirements: str
    diff_report: str
    updated_requirements: str


# ---------------------------------------------------------------------------
# LLM Factory + model fallback
# ---------------------------------------------------------------------------

_LAST_WORKING_MODEL: Optional[str] = None


def _get_llm(model: str, temperature: float = 0.2):
    api_key = os.environ.get("GEMINI_API_KEY", "")
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


def _build_model_candidates() -> List[str]:
    """Build an ordered, unique list of candidate models.

    Priority:
      1) Last working model in this process
      2) `GEMINI_MODEL` env var
      3) `GEMINI_MODEL_FALLBACKS` env var (comma-separated)
      4) Built-in MODELS list
    """
    global _LAST_WORKING_MODEL

    candidates: List[str] = []

    def _add(value: str) -> None:
        model_name = value.strip()
        if model_name and model_name not in candidates:
            candidates.append(model_name)

    if _LAST_WORKING_MODEL:
        _add(_LAST_WORKING_MODEL)

    env_model = os.environ.get("GEMINI_MODEL", "")
    if env_model:
        _add(env_model)

    env_fallbacks = os.environ.get("GEMINI_MODEL_FALLBACKS", "")
    if env_fallbacks:
        for item in env_fallbacks.split(","):
            _add(item)

    for item in MODELS:
        _add(item)

    return candidates


def _is_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    auth_markers = [
        "api key",
        "invalid key",
        "permission denied",
        "unauthenticated",
        "forbidden",
    ]
    return any(marker in text for marker in auth_markers)


def _invoke_with_available_model(
    messages: List[Any],
    temperature: float,
    stage: str,
) -> Tuple[Any, str]:
    """Invoke Gemini trying multiple models until one succeeds."""
    global _LAST_WORKING_MODEL

    candidates = _build_model_candidates()
    if not candidates:
        raise RuntimeError("No Gemini models configured")

    errors: List[str] = []

    for model_name in candidates:
        try:
            llm = _get_llm(model=model_name, temperature=temperature)
            response = llm.invoke(messages)
            if _LAST_WORKING_MODEL != model_name:
                print(
                    f"[AGENTS] {stage} using model: {model_name}",
                    flush=True,
                )
            _LAST_WORKING_MODEL = model_name
            return response, model_name
        except Exception as exc:
            errors.append(f"- {model_name}: {exc}")
            print(
                f"[AGENTS] {stage} model failed: {model_name} -> {exc}",
                flush=True,
            )
            if _is_auth_error(exc):
                raise RuntimeError(
                    "Gemini authentication/configuration error. "
                    "Check GEMINI_API_KEY and permissions."
                ) from exc

    error_text = "\n".join(errors[:8])
    raise RuntimeError(
        "No available Gemini model succeeded. Models tried:\n"
        f"{error_text}"
    )


# ---------------------------------------------------------------------------
# Creator Node
# ---------------------------------------------------------------------------

CREATOR_SYSTEM = f"""\
You are an expert UML Class Diagram architect specializing in DOMAIN MODELING.

Your task is to produce a JSON object conforming EXACTLY to this schema:

{SYSTEM_SPEC_SCHEMA}

CRITICAL RULES FOR DOMAIN MODELING:
- You MUST ONLY create classes that represent DOMAIN ENTITIES (business concepts).
- Examples of VALID domain classes: User, Order, Product, Payment, Menu, Reservation, Report.
- NEVER create classes for UI components: HeroSection, NavBar, Footer, Sidebar, Header,
  ContactForm, Button, Modal, Card, Banner, Layout, Page, Section, Widget, etc.
- NEVER create classes for technical infrastructure: Database, Server, API, Router,
  Controller, Service (as a class), Repository (unless it's a domain concept).
- Every class MUST directly represent a business entity mentioned in or implied by the requirements.
- Every class MUST have at least one attribute AND at least one method.
- Use PascalCase for class names, camelCase for attributes and methods.
- Types must be primitive (str, int, float, bool, Date) or reference another class name.
- Every relationship source/target must match an existing className.
- Cover ALL functional requirements. Map each requirement to at least one domain class.
- Do NOT include markdown fences or explanatory text — output ONLY the JSON.
- Inheritance should only be used when there is a clear IS-A relationship.
- Prefer Composition for strong ownership and Aggregation for weak ownership.
- Association is for general references between classes.

Think about the PROBLEM DOMAIN, not the solution/UI:
- What real-world entities does the system manage?
- What are the core business objects?
- What data does each entity own?
- What operations can be performed on each entity?
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

    user_content = f"Requirements:\n\n{state['requirements']}\n\nFeature: {state['feature_name']}"

    # Combine structural feedback and traceability feedback
    combined_feedback = ""
    if state["feedback"]:
        combined_feedback += state["feedback"]
    if state.get("traceability_feedback"):
        if combined_feedback:
            combined_feedback += "\n\n--- TRACEABILITY FEEDBACK ---\n"
        combined_feedback += state["traceability_feedback"]

    if combined_feedback and state["diagram_json"]:
        user_content += "\n\n" + CREATOR_WITH_FEEDBACK.format(
            feedback=combined_feedback,
            prev_diagram=state["diagram_json"],
        )

    messages = [
        SystemMessage(content=CREATOR_SYSTEM),
        HumanMessage(content=user_content),
    ]

    response, _ = _invoke_with_available_model(
        messages=messages,
        temperature=0.15,
        stage="Creator",
    )
    raw = response.content.strip()

    # Strip markdown fences if the LLM wraps the JSON
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    return {
        "diagram_json": raw,
        "iterations": iteration,
    }


# ---------------------------------------------------------------------------
# Reviewer Node (structural)
# ---------------------------------------------------------------------------

REVIEWER_SYSTEM = """\
You are a senior software architect reviewing a UML Class Diagram JSON for:

1. **JSON validity** — must parse as valid JSON.
2. **Structural correctness** — no dangling relationships, no empty classes.
3. **Naming conventions** — PascalCase classes, camelCase members.
4. **Relationship quality** — correct types and multiplicities.
5. **Domain purity** — classes must represent DOMAIN ENTITIES only.
   REJECT any class that represents a UI component (HeroSection, NavBar, Footer,
   Sidebar, Header, ContactForm, Button, Modal, Card, Banner, Layout, Page, etc.)
   or technical infrastructure (Database, Server, API, Router, Controller, etc.).

If the diagram is acceptable, respond with EXACTLY: APPROVED

If there are issues, provide a numbered list of specific fixes.
Do NOT output a corrected JSON — only the feedback.
"""


def reviewer_node(state: DiagramState) -> dict:
    """Review the diagram and either approve or provide feedback."""
    print("[AGENTS] Reviewer", flush=True)

    user_content = (
        f"Requirements:\n{state['requirements']}\n\n"
        f"Diagram JSON:\n```json\n{state['diagram_json']}\n```\n\n"
        "Review the diagram and respond with APPROVED or specific feedback."
    )

    messages = [
        SystemMessage(content=REVIEWER_SYSTEM),
        HumanMessage(content=user_content),
    ]

    response, _ = _invoke_with_available_model(
        messages=messages,
        temperature=0.1,
        stage="Reviewer",
    )
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
# Traceability Reviewer Node (NEW)
# ---------------------------------------------------------------------------

TRACEABILITY_REVIEWER_SYSTEM = """\
You are a requirements traceability auditor. Your job is to verify BIDIRECTIONAL
traceability between a requirements document and a UML class diagram.

For EVERY requirement in the requirements document:
- There MUST be at least one domain class that realizes it.
- The class must have attributes and/or methods that directly relate to the requirement.

For EVERY class in the diagram:
- It MUST be traceable to at least one requirement.
- If a class exists but has no corresponding requirement, it is an ORPHAN class.

Also check:
- NO class should represent a UI component (HeroSection, NavBar, Footer, Sidebar, etc.)
- NO class should represent technical infrastructure (Database, API, Router, etc.)
- Every class must represent a real-world business entity from the problem domain.

If traceability is complete and all classes are valid domain entities, respond with EXACTLY:
APPROVED

Otherwise, provide a numbered list of specific issues:
1. Requirement "X" has no corresponding class → suggest creating class "Y"
2. Class "Z" is an orphan (no requirement) → suggest removing or linking
3. Class "HeroSection" is a UI component → must be removed
"""


def traceability_reviewer_node(state: DiagramState) -> dict:
    """Validate bidirectional Requirement ↔ Class traceability."""
    print("[AGENTS] TraceabilityReviewer", flush=True)

    user_content = (
        f"Requirements document:\n```markdown\n{state['requirements']}\n```\n\n"
        f"Diagram JSON:\n```json\n{state['diagram_json']}\n```\n\n"
        "Verify bidirectional traceability. Respond with APPROVED or specific issues."
    )

    messages = [
        SystemMessage(content=TRACEABILITY_REVIEWER_SYSTEM),
        HumanMessage(content=user_content),
    ]

    response, _ = _invoke_with_available_model(
        messages=messages,
        temperature=0.1,
        stage="TraceabilityReviewer",
    )
    feedback = response.content.strip()
    approved = feedback.upper().startswith("APPROVED")

    print(
        f"[AGENTS] TraceabilityReviewer verdict: "
        f"{'APPROVED' if approved else feedback[:120]}...",
        flush=True,
    )

    return {
        "traceability_feedback": feedback,
        "traceability_approved": approved,
    }


# ---------------------------------------------------------------------------
# Traceability Generator Node (NEW)
# ---------------------------------------------------------------------------

TRACEABILITY_GENERATOR_SYSTEM = """\
You are a technical writer generating a Traceability Matrix in Markdown.

Given a requirements document and a class diagram JSON, produce a traceability.md
that maps every requirement to its implementing class(es) and vice versa.

Use this EXACT format (respond in the SAME LANGUAGE as the requirements document):

# Matriz de Trazabilidad — {feature_name}

## Resumen
- Total de requisitos: X
- Total de clases de dominio: Y
- Cobertura: 100% (or actual %)

## Mapeo Requisitos → Clases de Dominio

| ID Requisito | Descripción | Clase(s) de Dominio | Atributos Clave | Métodos Clave |
|-------------|-------------|---------------------|-----------------|---------------|
| REQ-1 | ... | ClassName1, ClassName2 | attr1, attr2 | method1() |

## Mapeo Clases de Dominio → Requisitos

| Clase | Requisito(s) Asociado(s) | Responsabilidad |
|-------|--------------------------|-----------------|
| ClassName1 | REQ-1, REQ-3 | Brief description |

## Validación
- ✅ Requisitos cubiertos: X/Y
- ✅ Clases con requisito: X/Y
- ⚠️ Requisitos sin clase: (list or "Ninguno")
- ⚠️ Clases sin requisito: (list or "Ninguno")

Output ONLY the markdown content. No markdown fences around the entire output.
"""


def traceability_generator_node(state: DiagramState) -> dict:
    """Generate traceability.md from the approved diagram and requirements."""
    print("[AGENTS] TraceabilityGenerator", flush=True)

    user_content = (
        f"Feature name: {state['feature_name']}\n\n"
        f"Requirements document:\n```markdown\n{state['requirements']}\n```\n\n"
        f"Approved diagram JSON:\n```json\n{state['diagram_json']}\n```\n\n"
        "Generate the traceability matrix markdown."
    )

    messages = [
        SystemMessage(content=TRACEABILITY_GENERATOR_SYSTEM),
        HumanMessage(content=user_content),
    ]

    response, _ = _invoke_with_available_model(
        messages=messages,
        temperature=0.1,
        stage="TraceabilityGenerator",
    )
    md = response.content.strip()

    # Strip markdown fences if the LLM wraps the output
    md = re.sub(r"^```(?:markdown)?\s*", "", md)
    md = re.sub(r"\s*```$", "", md)

    print(f"[AGENTS] Traceability matrix generated ({len(md)} chars)", flush=True)

    return {
        "traceability_md": md,
    }


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

def structural_router(state: DiagramState) -> str:
    """After Reviewer: loop back to Creator or proceed to TraceabilityReviewer."""
    if state["approved"]:
        return "traceability_reviewer"
    if state["iterations"] >= 3:
        return "traceability_reviewer"  # Move on even if not perfect
    return "creator"


def traceability_router(state: DiagramState) -> str:
    """After TraceabilityReviewer: approve or loop back to Creator."""
    if state["traceability_approved"]:
        return "traceability_generator"
    # Allow at most 1 extra Creator iteration for traceability fixes
    if state["iterations"] >= 4:
        return "traceability_generator"  # Generate anyway
    return "creator"


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------

def run_diagram_pipeline(
    feature_name: str,
    requirements_text: str,
) -> Dict[str, Any]:
    """
    Run the Creator→Reviewer→TraceabilityReviewer loop and return a parsed
    SystemSpec dict plus traceability markdown.

    Returns a dict with keys: spec (the SystemSpec), traceability_md (the markdown).
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
    workflow.add_node("traceability_reviewer", traceability_reviewer_node)
    workflow.add_node("traceability_generator", traceability_generator_node)

    workflow.set_entry_point("creator")
    workflow.add_edge("creator", "reviewer")
    workflow.add_conditional_edges("reviewer", structural_router)
    workflow.add_conditional_edges("traceability_reviewer", traceability_router)
    workflow.add_edge("traceability_generator", END)

    app = workflow.compile()

    initial: DiagramState = {
        "requirements": requirements_text,
        "feature_name": feature_name,
        "diagram_json": None,
        "feedback": "",
        "iterations": 0,
        "approved": False,
        "traceability_approved": False,
        "traceability_feedback": "",
        "traceability_md": "",
    }

    final_state = app.invoke(initial)

    raw_json = final_state["diagram_json"]
    iterations = final_state["iterations"]
    approved = final_state["approved"]
    trace_approved = final_state["traceability_approved"]

    print(
        f"[AGENTS] Pipeline done — {iterations} iterations, "
        f"structural_approved={approved}, traceability_approved={trace_approved}",
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

    return {
        "spec": spec,
        "traceability_md": final_state.get("traceability_md", ""),
    }


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
    diff_response, _ = _invoke_with_available_model(
        messages=diff_messages,
        temperature=0.1,
        stage="Sync-Diff",
    )
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
    sync_response, _ = _invoke_with_available_model(
        messages=sync_messages,
        temperature=0.1,
        stage="Sync-Update",
    )
    updated = sync_response.content.strip()

    # Strip markdown fences
    updated = re.sub(r"^```(?:markdown)?\s*", "", updated)
    updated = re.sub(r"\s*```$", "", updated)

    print(f"[SYNC] Requirements updated ({len(updated)} chars)", flush=True)
    return updated
