"""MCP prompts: ready-made instructions that put the model into the review protocol."""
from __future__ import annotations

from typing import Any, Dict, List

from ..errors import InputError

RULES = (
    "Three rules you cannot break: (1) No look, no claim - a finding, a negative or a comparison verdict may only be written after "
    "page_view returned the page that shows it. (2) Every region and claim carries StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID, "
    "native row/col and the inspected page path. (3) Numbers come only from the measure tool; never estimate a size. "
    "If a rule cannot be met for a region, record it as limited or not_covered with the reason. Never assign a stage, Lung-RADS, "
    "Fleischner, RECIST, PERCIST or RANO category unless explicitly asked and the checklist prerequisites are met."
)


def _prompt(name: str, title: str, description: str, args: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"name": name, "title": title, "description": description, "arguments": args}


PROMPTS: List[Dict[str, Any]] = [
    _prompt("review_study", "Systematic preliminary review",
            "Image-first review of one DICOM study with the OpenRadiology tools; ends with a professional report and a patient guide.",
            [{"name": "study_folder", "description": "Absolute path of the study folder", "required": True},
             {"name": "language", "description": "Report language (default from configuration)", "required": False},
             {"name": "clinical_question", "description": "Why the study was ordered, if known", "required": False}]),
    _prompt("compare_studies", "Chronological comparison",
            "Interval-change review of two or more studies of the same patient; every verdict needs evidence at both endpoints.",
            [{"name": "study_folders", "description": "Comma-separated absolute paths", "required": True},
             {"name": "language", "description": "Report language", "required": False}]),
    _prompt("explain_for_patient", "Answer a patient's questions",
            "Explain a finished review to a patient or relative using only the generated guide; calm, accurate, no new claims.",
            [{"name": "session", "description": "Finished session id", "required": True}]),
    _prompt("blinded_audit", "Blinded audit of a study",
            "Read without the reference report, seal the review, then score against the report with the RADPEER-like rubric.",
            [{"name": "study_folder", "description": "Absolute path of the study folder", "required": True}]),
]


def list_prompts() -> List[Dict[str, Any]]:
    return PROMPTS


def get_prompt(name: str, args: Dict[str, str]) -> Dict[str, Any]:
    args = args or {}
    if name == "review_study":
        folder = _need(args, "study_folder")
        lang = args.get("language")
        q = args.get("clinical_question")
        body = (
            f"You are reviewing the DICOM study in `{folder}` with the OpenRadiology MCP server. {RULES}\n\n"
            f"{'Clinical question: ' + q + chr(10) if q else ''}"
            "Procedure:\n"
            "1. openrad_doctor; fix anything that is not OK.\n"
            "2. Read the resource openrad://checklist/<modality> for every modality in the study (ct_thorax, pet_ct, brain_mri) and openrad://checklist/lessons.\n"
            f"3. session_open with studies=[\"{folder}\"]" + (f", lang=\"{lang}\"" if lang else "") + "; keep the session id.\n"
            "4. study_inventory; choose the diagnostic series; session_set_series for every series (read with required_passes exactly as you will render, "
            "excluded or unsupported with reason).\n"
            "5. Render according to session_plan (render_ct with grid 3x3 for the systematic lung pass and 2x2 for soft/bone; render_pet; render_mr). "
            "Then session_register and page_view every page, superior to inferior, one anatomical system at a time.\n"
            "6. For every candidate: zoom on consecutive slices and in a second plane; measure with the right lesion_type; cite the evidence sha256.\n"
            "7. session_set_region for every region (finding/no_finding with refs and pages; limited/not_covered with reason); session_add_claim for every impression item "
            "with a four-part patient explanation; session_set_meta for reader, context_disclosure, limitations, patient_context, patient_limitations, reading_complete.\n"
            "8. session_check until the error list is empty, then session_finish. Read both documents end to end and reconcile dates, sides, sizes and negations.\n"
            "Report technical alerts from session_status in the limitations. Speak as a preliminary reviewer, never as the physician of record."
        )
        return {"description": "Systematic image-first review", "messages": [{"role": "user", "content": {"type": "text", "text": body}}]}
    if name == "compare_studies":
        folders = [f.strip() for f in _need(args, "study_folders").split(",") if f.strip()]
        lang = args.get("language")
        body = (
            f"You are comparing {len(folders)} studies of the same patient with the OpenRadiology MCP server: " + ", ".join(f"`{f}`" for f in folders) + f". {RULES}\n\n"
            "Chronology comes from DICOM headers (session_open sorts them); never trust folder names. "
            f"Open one comparison session with all folders" + (f" and lang=\"{lang}\"" if lang else "") + ". "
            "Render and view every study fully before comparing. For each claim record comparison.status (new, increased, decreased, stable, resolved, indeterminate, not_comparable) "
            "with from_study_uid/to_study_uid and a timeline entry for every study; a change verdict needs refs at both endpoints (old location for new, current location for resolved). "
            "State growth in millimetres with the interval in months; a difference below 2 mm average diameter is 'no measurable change'. "
            "Set session comparison {status, reason, explanation} via session_set_meta before session_finish."
        )
        return {"description": "Chronological comparison", "messages": [{"role": "user", "content": {"type": "text", "text": body}}]}
    if name == "explain_for_patient":
        session = _need(args, "session")
        body = (
            f"Read the resource openrad://session/{session} and the generated guide referenced in report_file/guide_file. Answer the patient's or relative's questions "
            "using only what the guide and report state. Calm, concrete, short sentences; explain the term, then the finding; separate what is certain from what is not; "
            "never add findings, prognosis or treatment; end by pointing to the physician who reviews the images. If a question goes beyond the documents, say so."
        )
        return {"description": "Patient-facing explanation", "messages": [{"role": "user", "content": {"type": "text", "text": body}}]}
    if name == "blinded_audit":
        folder = _need(args, "study_folder")
        body = (
            f"Blinded audit of `{folder}`. Read openrad://checklist/blind_audit first. Do not open any reference report or prior analysis. Write a leakage statement "
            "(everything you already know about this patient). Perform the full review_study procedure. Before unblinding, seal the professional report with "
            "`openrad lock <report>` (CLI) and record the hash. Only then open the reference report and score matched/partial/missed/overcalled with clinical importance."
        )
        return {"description": "Blinded audit", "messages": [{"role": "user", "content": {"type": "text", "text": body}}]}
    raise InputError(f"unknown prompt '{name}'")


def _need(args: Dict[str, str], key: str) -> str:
    if not args.get(key):
        raise InputError(f"prompt argument '{key}' is required")
    return str(args[key])
