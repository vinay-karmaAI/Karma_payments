from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
import google.generativeai as genai

from log_config import logger, audit_log


load_dotenv(dotenv_path=Path(__file__).parent / ".env")


_SYSTEM_PROMPT = """
You are a senior payments reconciliation analyst with deep expertise in
fintech, banking operations, and fraud detection.

You receive structured JSON output from an automated reconciliation engine
and produce:

1. A concise executive summary (3-5 sentences)
2. Root-cause analysis for each gap type found
3. Prioritised remediation recommendations
4. Risk assessment (financial exposure, compliance risk)

Respond ONLY in valid JSON matching this schema exactly:

{
  "executive_summary": "string",
  "risk_level": "CRITICAL|HIGH|MEDIUM|LOW",
  "total_financial_exposure": "string (USD)",
  "root_causes": [
    {
      "gap_type": "string",
      "root_cause": "string",
      "likelihood": "string"
    }
  ],
  "recommendations": [
    {
      "priority": 1,
      "action": "string",
      "owner": "string",
      "timeline": "string"
    }
  ],
  "production_blind_spots": ["string"],
  "key_metrics": {
    "reconciliation_rate": "string",
    "variance_usd": "string",
    "critical_issues": 0
  }
}
"""


def _build_prompt(recon_result: dict[str, Any], context: str | None = None) -> str:
    payload = {
        "summary": recon_result.get("summary", {}),
        "gap_stats": recon_result.get("stats", {}),
        "gaps": recon_result.get("gaps", [])[:40],
    }
    prompt = f"Reconciliation Data:\n\n{json.dumps(payload, indent=2)}\n\nAnalyse the reconciliation findings carefully."
    if context:
        prompt += f"\n\nAdditional Context:\n{context}"
    return prompt


def _sanitize_history(history: list[dict] | None) -> list[dict]:
    clean_history: list[dict] = []
    for entry in history or []:
        role = entry.get("role")
        if role not in ["user", "model"]:
            continue
        clean_parts = []
        for part in entry.get("parts", []):
            if isinstance(part, str) and part.strip():
                clean_parts.append({"text": part})
            elif isinstance(part, dict):
                text = part.get("text", "")
                if isinstance(text, str) and text.strip():
                    clean_parts.append({"text": text})
        if clean_parts:
            clean_history.append({"role": role, "parts": clean_parts})
    return clean_history


def _is_key_valid(api_key: str | None) -> bool:
    return bool(api_key and api_key.strip())


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def analyse_gaps(
    api_key: str | None,
    recon_result: dict[str, Any],
    context: str | None = None,
) -> dict[str, Any]:
    if not _is_key_valid(api_key):
        logger.warning("GEMINI_API_KEY not configured — returning mock analysis")
        return _mock_analysis(recon_result)

    if not recon_result:
        logger.warning("Empty reconciliation result received")
        return _mock_analysis(recon_result)

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=_SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )

        logger.info("Sending reconciliation data to Gemini")
        response = model.generate_content(_build_prompt(recon_result, context))
        raw_text = response.text.strip()

        analysis = _extract_json(raw_text)
        audit_log(
            "GEMINI_ANALYSIS_COMPLETE",
            source="gemini-2.5-flash",
            gap_count=len(recon_result.get("gaps", [])),
        )
        logger.info("Gemini analysis completed successfully")
        return {"source": "gemini-2.5-flash", **analysis}

    except json.JSONDecodeError as exc:
        logger.exception("Failed to parse Gemini JSON response")
        return {**_mock_analysis(recon_result), "error": f"Invalid JSON response: {exc}", "source": "fallback"}

    except Exception as exc:
        logger.exception("Gemini API error")
        return {**_mock_analysis(recon_result), "error": str(exc), "source": "fallback"}


def chat_with_data(
    api_key: str | None,
    question: str,
    recon_result: dict[str, Any],
    history: list[dict] | None = None,
) -> str:
    if not _is_key_valid(api_key):
        logger.warning("GEMINI_API_KEY not configured — AI chat unavailable")
        return "AI analysis is unavailable. Configure GEMINI_API_KEY in backend/.env to enable this feature."

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=(
                "You are a payments reconciliation expert. "
                "Answer questions about reconciliation findings. "
                "Be concise and accurate. Mention txn_ids and amounts when relevant. "
                "Do not hallucinate data."
            ),
        )

        clean_history = _sanitize_history(history)
        chat = model.start_chat(history=clean_history)

        if not clean_history:
            full_question = (
                f"Reconciliation Context:\n\nSummary:\n{json.dumps(recon_result.get('summary', {}), indent=2)}\n\n"
                f"Gaps:\n{json.dumps(recon_result.get('gaps', [])[:30], indent=2)}\n\nQuestion:\n{question}"
            )
        else:
            full_question = question

        response = chat.send_message(full_question)
        logger.info(f"Gemini chat response generated for: {question[:100]}")
        return response.text

    except Exception as exc:
        logger.exception("Gemini chat error")
        return f"Error generating AI response: {exc}"


def _mock_analysis(recon_result: dict[str, Any]) -> dict[str, Any]:
    summary = recon_result.get("summary", {})
    gaps = recon_result.get("gaps", [])
    critical = summary.get("critical_gaps", 0)

    return {
        "source": "fallback",
        "executive_summary": (
            f"Reconciliation identified {summary.get('total_gaps', 0)} discrepancies across "
            f"{summary.get('total_platform_txns', 0)} transactions. "
            f"Total variance is USD {summary.get('total_variance_usd', '0.00')}."
        ),
        "risk_level": "CRITICAL" if critical > 0 else "MEDIUM",
        "total_financial_exposure": summary.get("total_variance_usd", "0.00"),
        "root_causes": [
            {
                "gap_type": gap.get("gap_type", "UNKNOWN"),
                "root_cause": "Configure GEMINI_API_KEY for detailed root-cause analysis.",
                "likelihood": "Unknown",
            }
            for gap in gaps[:4]
        ],
        "recommendations": [
            {
                "priority": 1,
                "action": "Configure GEMINI_API_KEY in backend/.env for AI-powered analysis.",
                "owner": "Engineering",
                "timeline": "Immediate",
            },
            {
                "priority": 2,
                "action": "Review all CRITICAL severity reconciliation gaps.",
                "owner": "Finance Operations",
                "timeline": "Today",
            },
        ],
        "production_blind_spots": [
            "FX conversion mismatches are not detected — multi-currency settlements may reconcile incorrectly when exchange rates differ between booking and settlement date.",
            "Chargeback and dispute lifecycle is not modelled — a transaction that is later reversed via chargeback will appear as an orphan bank debit with no platform counterpart.",
            "Real-time intra-day settlement drift is invisible — the engine operates on a static month-end snapshot, so partial-day batches that straddle the cut-off are not flagged until the following run.",
        ],
        "key_metrics": {
            "reconciliation_rate": summary.get("reconciliation_rate", "N/A"),
            "variance_usd": summary.get("total_variance_usd", "0.00"),
            "critical_issues": critical,
        },
    }
