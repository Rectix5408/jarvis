"""Bounded, explicit Anthropic response test. SPDX-License-Identifier: Apache-2.0."""
import time
import httpx

from backend.llm import clean_api_key, scrub_secrets


async def test_anthropic_response(api_key: str, model: str) -> dict:
    key = clean_api_key(api_key)
    model = (model or "").strip()
    if not key or "*" in key:
        return {"success": False, "error": "API-Key fehlt oder ist maskiert."}
    if not model:
        return {"success": False, "error": "Modell fehlt."}
    started = time.monotonic()
    try:
        # Fixed provider URL and no redirects: credentials never go to a form-supplied host.
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={"model": model, "max_tokens": 32,
                      "messages": [{"role": "user", "content": "Reply with only OK."}]},
            )
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            messages = {
                400: "Anfrage abgelehnt. Modellparameter oder API-Guthaben in der Anthropic Console pruefen.",
                401: "API-Key ungueltig oder widerrufen.",
                403: "API-Key hat keine Berechtigung fuer dieses Modell.",
                404: "Modell nicht gefunden. Modellliste neu laden.",
                429: "API-Limit erreicht. Spaeter erneut versuchen.",
            }
            # Never relay an upstream error body; it may reflect credentials.
            return {"success": False, "error": messages.get(response.status_code, "Anthropic antwortet nicht erfolgreich."),
                    "http_status": response.status_code, "latency_ms": latency}
        data = response.json()
        text = " ".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text").strip()
        if not text:
            return {"success": False, "error": "Keine Textantwort erhalten.", "latency_ms": latency}
        usage = data.get("usage", {})
        return {"success": True, "message": "Anthropic hat vom Jarvis-Server aus geantwortet.",
                "response": scrub_secrets(text[:200], api_key, key), "model": model, "latency_ms": latency,
                "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}}
    except httpx.TimeoutException:
        return {"success": False, "error": "Zeitlimit beim Antworttest erreicht (30 Sekunden)."}
    except Exception:
        return {"success": False, "error": "Antworttest fehlgeschlagen. Serververbindung pruefen."}
