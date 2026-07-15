"""
TextBack analyze API.

Reproduces the original Supabase `analyze-message` edge function as a first-
class Flask endpoint so the React frontend can talk to this backend directly.

Design goals:
  * Works out of the box locally. If a real GEMINI_API_KEY is configured it uses
    Google Gemini for high-quality analysis; otherwise it falls back to a
    deterministic local heuristic engine so the app NEVER errors out in a demo.
  * Returns the exact JSON schema the frontend expects (see AnalysisData in
    src/components/AnalysisResult.tsx).
  * Persists every analysis to SQLite via the Analysis model.
"""
import json
import re
import logging

import requests
from flask import Blueprint, request, jsonify

from __init__ import app

logger = logging.getLogger(__name__)

analyze_api = Blueprint('analyze_api', __name__)

# Valid enum values the frontend renders. We normalize AI/heuristic output to
# these so the UI never receives something it can't display.
REWRITE_STYLES = ["confident", "chill", "funny", "flirty"]
STYLE_EMOJI = {"confident": "💪", "chill": "😎", "funny": "😂", "flirty": "🔥"}
EMOTIONAL_TONES = ["Confident", "Needy", "Passive", "Aggressive", "Playful",
                   "Flirty", "Defensive", "Dry", "Anxious", "Warm", "Neutral"]


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
def _gemini_key():
    key = (app.config.get('GEMINI_API_KEY')
           or __import__('os').environ.get('GEMINI_API_KEY') or '').strip()
    # 'xxxxx' is the placeholder shipped in .env.example — treat as unset.
    if not key or key.lower() in ('xxxxx', 'your_key_here'):
        return None
    return key


def _gemini_server():
    import os
    return (os.environ.get('GEMINI_SERVER')
            or 'https://generativelanguage.googleapis.com/v1beta/models/'
               'gemini-flash-latest:generateContent')


def _build_system_prompt(context, tone_bias):
    tone_bias_instruction = (
        f'\nTONE BIAS: The user wants rewrites biased toward "{tone_bias}" on a '
        f'scale. Adjust all rewrites and the improvedVersion to lean into this '
        f'tone direction.' if tone_bias else '')
    return f"""You are TextBack's AI engine — a blunt, Gen-Z-friendly texting coach that prevents texting regret. You analyze messages people are about to send and help them communicate better.

TASK: Analyze the user's message and return a JSON object with ALL of these fields:

{{
  "sendDecision": "send" | "edit" | "dont_send",
  "sendDecisionReason": string (1 sentence explaining the decision),
  "safeToSend": number (0-100),
  "safeToSendLabel": "Safe" | "Risky" | "Danger",
  "safeToSendReason": string (1 sentence),
  "replyProbability": number (0-100, estimated chance of getting a reply),
  "replyProbabilityReason": string (1 sentence explaining why, referencing specific message traits),
  "turnOffs": [string] (1-3 concise bullets of what might turn the recipient off, specific to THIS message. Empty array if none),
  "emotionalTone": string ("Confident", "Needy", "Passive", "Aggressive", "Playful", "Flirty", "Defensive", "Dry", "Anxious", "Warm", "Neutral"),
  "emotionalToneExplanation": string (1 sentence referencing specific words),
  "regretRisk": "low" | "medium" | "high",
  "regretWarning": string | null,
  "quickFixes": [string] (2-3 actionable fixes),
  "improvedVersion": string (the single best rewrite of the message — natural, concise, clearly better),
  "tone": string (same as emotionalTone),
  "clarity": number (1-10),
  "confidence": number (1-10),
  "replyLikelihood": "Low" | "Medium" | "High",
  "feedback": string (1-2 sentences, direct),
  "insights": [string, string, string] (3 specific, actionable insights),
  "rewrites": [
    {{ "style": "confident", "text": string, "emoji": "💪" }},
    {{ "style": "chill", "text": string, "emoji": "😎" }},
    {{ "style": "funny", "text": string, "emoji": "😂" }},
    {{ "style": "flirty", "text": string, "emoji": "🔥" }}
  ],
  "nextMessages": [
    {{ "scenario": string, "suggestion": string }},
    {{ "scenario": string, "suggestion": string }},
    {{ "scenario": string, "suggestion": string }}
  ],
  "mode": "normal" | "new_person" | "argument",
  "modeAdvice": string | null
}}

SEND DECISION RULES:
- "send": safeToSend >= 70, no major tone issues
- "edit": safeToSend 40-69 OR has fixable problems
- "dont_send": safeToSend < 40 OR high regret risk OR aggressive/impulsive tone

REPLY PROBABILITY SCORING:
- Consider: Does it ask a question? Is it engaging? Is it too dry/short? Does it invite response?
- A one-word message with no question = low probability
- An engaging question with good tone = high probability

TURN-OFF DETECTION:
- Be specific to the exact message words
- If the message is fine, return empty array

IMPROVED VERSION:
- Single best rewrite that fixes the main issues, sounds natural and human, keeps the user's core intent

NEXT MESSAGES:
- Generate realistic follow-up suggestions based on likely replies. Each scenario should be a different outcome.

CONTEXT-AWARE:
- Context: {context or "friend"}
- Crush/new person: confident, playful, avoid neediness
- Work: professional, clear, neutral
- Ex: cautious, emotionally controlled
- Friend: casual, natural
{tone_bias_instruction}

REWRITE RULES:
- Sound like real iMessage texts — casual, natural, 1-2 sentences max
- Each rewrite must be meaningfully different
- NEVER use generic filler

Return ONLY valid JSON. No markdown, no explanation."""


def _analyze_with_gemini(message, context, tone_bias):
    """Call Gemini and return the parsed dict, or None on any failure."""
    key = _gemini_key()
    if not key:
        return None
    url = f"{_gemini_server()}?key={key}"
    payload = {
        "systemInstruction": {
            "parts": [{"text": _build_system_prompt(context, tone_bias)}]
        },
        "contents": [
            {"role": "user",
             "parts": [{"text": f'Analyze this message: "{message}"'}]}
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.8,
        },
    }
    import time
    # Retry briefly on transient overload/rate-limit (503/429/500) — Google's
    # gateway occasionally spikes. After the retries we fall back to the local
    # engine, so the user never sees an error either way.
    resp = None
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload,
                                 headers={"Content-Type": "application/json"},
                                 timeout=45)
        except requests.RequestException as e:
            logger.warning("Gemini request failed (attempt %s): %s", attempt + 1, e)
            resp = None
            time.sleep(0.6 * (attempt + 1))
            continue
        if resp.status_code == 200:
            break
        if resp.status_code in (429, 500, 502, 503):
            logger.warning("Gemini transient %s (attempt %s), retrying...",
                           resp.status_code, attempt + 1)
            time.sleep(0.6 * (attempt + 1))
            continue
        # Non-retryable error (e.g. 400/403) — stop and fall back.
        logger.warning("Gemini returned %s: %s", resp.status_code, resp.text[:300])
        return None

    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        text = re.sub(r'^```json\s*', '', text.strip(), flags=re.I)
        text = re.sub(r'```\s*$', '', text).strip()
        return json.loads(text)
    except (KeyError, IndexError, ValueError) as e:
        logger.warning("Gemini response parse failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Local heuristic engine (fallback — no external calls, always works)
# ---------------------------------------------------------------------------
NEEDY_WORDS = ["please", "please please", "i miss you", "why didn't you", "why havent you",
               "why haven't you", "are you mad", "did i do something", "i'm sorry",
               "im sorry", "sorry", "you never", "you always", "i need you", "come back"]
AGGRESSIVE_WORDS = ["hate", "stupid", "shut up", "whatever", "idc", "screw you",
                    "wtf", "fuck", "pissed", "annoying", "done with you"]
FLIRTY_WORDS = ["cute", "miss you", "thinking about you", "you up", "hey you", "😘", "😉", "🔥", "babe"]
WARM_WORDS = ["thank you", "thanks", "appreciate", "proud of you", "congrats", "love", "hope"]


def _heuristic(message, context, tone_bias):
    msg = message.strip()
    low = msg.lower()
    words = re.findall(r"\w+", low)
    n_words = len(words)
    has_question = "?" in msg
    exclaim = msg.count("!")
    caps_ratio = (sum(1 for c in msg if c.isupper()) / max(1, sum(1 for c in msg if c.isalpha())))
    is_needy = any(w in low for w in NEEDY_WORDS)
    is_aggressive = any(w in low for w in AGGRESSIVE_WORDS) or caps_ratio > 0.6 or exclaim >= 3
    is_flirty = any(w in low for w in FLIRTY_WORDS)
    is_warm = any(w in low for w in WARM_WORDS)

    # Base score
    score = 65
    if has_question:
        score += 12
    if is_warm:
        score += 10
    if 3 <= n_words <= 40:
        score += 5
    if n_words < 2:
        score -= 15
    if n_words > 60:
        score -= 10
    if is_needy:
        score -= 25
    if is_aggressive:
        score -= 35
    if exclaim >= 2:
        score -= 5
    if caps_ratio > 0.5 and sum(1 for c in msg if c.isalpha()) > 5:
        score -= 10
    score = max(3, min(98, score))

    # Emotional tone
    if is_aggressive:
        tone = "Aggressive"
        tone_expl = "The wording and punctuation read as heated and confrontational."
    elif is_needy:
        tone = "Needy"
        tone_expl = "Phrases here signal insecurity and may put pressure on the reader."
    elif is_flirty:
        tone = "Flirty"
        tone_expl = "Playful, forward wording gives this a flirty energy."
    elif is_warm:
        tone = "Warm"
        tone_expl = "Kind, appreciative wording makes this feel warm and genuine."
    elif n_words <= 2:
        tone = "Dry"
        tone_expl = "It's very short, which can come across as low-effort or dry."
    elif has_question:
        tone = "Confident"
        tone_expl = "A clear question invites a response and reads as confident."
    else:
        tone = "Neutral"
        tone_expl = "The message is even-keeled without a strong emotional charge."

    # Reply probability
    reply = 50
    if has_question:
        reply += 25
    if is_warm or is_flirty:
        reply += 10
    if n_words <= 2:
        reply -= 25
    if is_needy:
        reply -= 15
    if is_aggressive:
        reply -= 20
    reply = max(5, min(95, reply))

    # Send decision + labels
    if score >= 70 and not is_aggressive:
        decision, decision_reason = "send", "This reads clearly and shouldn't cause problems."
        label = "Safe"
        regret = "low"
        regret_warning = None
    elif score < 40 or is_aggressive:
        decision, decision_reason = "dont_send", "The tone here risks a reaction you might regret."
        label = "Danger"
        regret = "high"
        regret_warning = "This could escalate or come off worse than you intend. Give it a beat before sending."
    else:
        decision, decision_reason = "edit", "It's close — a small tweak makes it land better."
        label = "Risky"
        regret = "medium"
        regret_warning = "There's a decent chance this reads differently than you mean it to."

    # Turn-offs
    turn_offs = []
    if is_needy:
        turn_offs.append("Comes across as needy or seeking reassurance")
    if is_aggressive:
        turn_offs.append("Feels confrontational — likely to raise defenses")
    if n_words <= 2:
        turn_offs.append("Too short to invite a real reply")
    if caps_ratio > 0.5 and sum(1 for c in msg if c.isalpha()) > 5:
        turn_offs.append("All-caps reads as shouting")
    turn_offs = turn_offs[:3]

    # Quick fixes
    quick_fixes = []
    if not has_question:
        quick_fixes.append("Add a question so they have an easy way to reply")
    if is_needy:
        quick_fixes.append("Drop the apology/reassurance-seeking and keep it light")
    if is_aggressive:
        quick_fixes.append("Cool off and restate the point without the heat")
    if n_words <= 2:
        quick_fixes.append("Give a little more context so it doesn't feel abrupt")
    if not quick_fixes:
        quick_fixes = ["Tighten the phrasing", "Lead with the main point"]
    quick_fixes = quick_fixes[:3]

    # Rewrites — context aware, simple transformations
    base = msg.rstrip(".!? ") or "hey"
    ctx = context or "friend"
    confident = f"{base.capitalize()} — let me know what works for you." if has_question else f"{base.capitalize()}. Let me know your thoughts."
    chill = f"hey, {base.lower()} — no rush 🙂"
    funny = f"{base.lower()}... or should I take the silence personally 😂"
    flirty = f"{base.lower()} 😉 was hoping to hear from you"
    if ctx == "coworker":
        confident = f"{base.capitalize()}. Happy to adjust if needed."
        flirty = f"{base.capitalize()}. Let me know what you think."  # keep it professional
        funny = f"{base.capitalize()} — promise it's quick 🙂"
    elif ctx == "ex":
        flirty = f"{base.lower()}. hope you're doing well."
        chill = f"hey — {base.lower()}, whenever's good."

    rewrites = [
        {"style": "confident", "text": confident, "emoji": STYLE_EMOJI["confident"]},
        {"style": "chill", "text": chill, "emoji": STYLE_EMOJI["chill"]},
        {"style": "funny", "text": funny, "emoji": STYLE_EMOJI["funny"]},
        {"style": "flirty", "text": flirty, "emoji": STYLE_EMOJI["flirty"]},
    ]

    # Tone-bias nudge for the improved version
    improved = confident
    if tone_bias:
        tb = tone_bias.lower()
        if "flirt" in tb:
            improved = flirty
        elif "chill" in tb or "casual" in tb:
            improved = chill
        elif "fun" in tb or "play" in tb:
            improved = funny

    mode = "new_person" if ctx == "crush" else ("argument" if is_aggressive else "normal")
    mode_advice = None
    if mode == "new_person":
        mode_advice = "Early on, keep it confident and low-pressure — curiosity beats intensity."
    elif mode == "argument":
        mode_advice = "You're heated. Sending now usually makes it worse — draft it, wait 10 minutes."

    return {
        "sendDecision": decision,
        "sendDecisionReason": decision_reason,
        "safeToSend": score,
        "safeToSendLabel": label,
        "safeToSendReason": f"Scored {score}/100 based on tone, clarity, and how it's likely to land.",
        "replyProbability": reply,
        "replyProbabilityReason": (
            "It asks a clear question, which invites a reply." if has_question
            else "There's no question or hook, so it's easy to leave on read."),
        "turnOffs": turn_offs,
        "emotionalTone": tone,
        "emotionalToneExplanation": tone_expl,
        "regretRisk": regret,
        "regretWarning": regret_warning,
        "quickFixes": quick_fixes,
        "improvedVersion": improved,
        "tone": tone,
        "clarity": max(1, min(10, round(score / 10))),
        "confidence": max(1, min(10, round((score + reply) / 20))),
        "replyLikelihood": "High" if reply >= 66 else ("Medium" if reply >= 40 else "Low"),
        "feedback": decision_reason + " " + (turn_offs[0] if turn_offs else "Overall it's in good shape."),
        "insights": [
            (f"Reply odds ~{reply}% — {'a question would raise them' if not has_question else 'the question helps'}."),
            (f"Tone reads as {tone.lower()}. {tone_expl}"),
            (quick_fixes[0] if quick_fixes else "Keep it concise and clear."),
        ],
        "rewrites": rewrites,
        "nextMessages": [
            {"scenario": "If they reply positively", "suggestion": "Keep the momentum — ask a light follow-up question."},
            {"scenario": "If they hesitate", "suggestion": "Give them an easy out: 'no worries if you're busy!'"},
            {"scenario": "If no reply", "suggestion": "Wait a day, then send something new and low-pressure — don't double-text anxiously."},
        ],
        "mode": mode,
        "modeAdvice": mode_advice,
        "engine": "heuristic",
    }


# ---------------------------------------------------------------------------
# Normalization — guarantee the exact schema the frontend expects
# ---------------------------------------------------------------------------
def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def _normalize(data, fallback):
    """Coerce a (possibly AI-produced) dict into the strict frontend schema,
    filling any missing/invalid field from the heuristic fallback."""
    if not isinstance(data, dict):
        return fallback
    out = {}
    out["sendDecision"] = data.get("sendDecision") if data.get("sendDecision") in ("send", "edit", "dont_send") else fallback["sendDecision"]
    out["sendDecisionReason"] = str(data.get("sendDecisionReason") or fallback["sendDecisionReason"])
    out["safeToSend"] = _clamp(data.get("safeToSend"), 0, 100, fallback["safeToSend"])
    out["safeToSendLabel"] = data.get("safeToSendLabel") if data.get("safeToSendLabel") in ("Safe", "Risky", "Danger") else fallback["safeToSendLabel"]
    out["safeToSendReason"] = str(data.get("safeToSendReason") or fallback["safeToSendReason"])
    out["replyProbability"] = _clamp(data.get("replyProbability"), 0, 100, fallback["replyProbability"])
    out["replyProbabilityReason"] = str(data.get("replyProbabilityReason") or fallback["replyProbabilityReason"])
    out["turnOffs"] = [str(x) for x in data.get("turnOffs", [])][:3] if isinstance(data.get("turnOffs"), list) else fallback["turnOffs"]
    tone = data.get("emotionalTone") or data.get("tone")
    out["emotionalTone"] = tone if tone in EMOTIONAL_TONES else fallback["emotionalTone"]
    out["emotionalToneExplanation"] = str(data.get("emotionalToneExplanation") or fallback["emotionalToneExplanation"])
    out["regretRisk"] = data.get("regretRisk") if data.get("regretRisk") in ("low", "medium", "high") else fallback["regretRisk"]
    rw = data.get("regretWarning", fallback["regretWarning"])
    out["regretWarning"] = str(rw) if rw else None
    out["quickFixes"] = [str(x) for x in data.get("quickFixes", [])][:3] if isinstance(data.get("quickFixes"), list) and data.get("quickFixes") else fallback["quickFixes"]
    out["improvedVersion"] = str(data.get("improvedVersion") or fallback["improvedVersion"])
    out["tone"] = out["emotionalTone"]
    out["clarity"] = _clamp(data.get("clarity"), 1, 10, fallback["clarity"])
    out["confidence"] = _clamp(data.get("confidence"), 1, 10, fallback["confidence"])
    out["replyLikelihood"] = data.get("replyLikelihood") if data.get("replyLikelihood") in ("Low", "Medium", "High") else fallback["replyLikelihood"]
    out["feedback"] = str(data.get("feedback") or fallback["feedback"])
    ins = data.get("insights")
    out["insights"] = [str(x) for x in ins][:3] if isinstance(ins, list) and ins else fallback["insights"]

    # Rewrites — always deliver all four styles the UI renders.
    rewrites_in = {r.get("style"): r for r in data.get("rewrites", []) if isinstance(r, dict)} if isinstance(data.get("rewrites"), list) else {}
    fb_rewrites = {r["style"]: r for r in fallback["rewrites"]}
    out["rewrites"] = []
    for style in REWRITE_STYLES:
        r = rewrites_in.get(style) or fb_rewrites[style]
        out["rewrites"].append({
            "style": style,
            "text": str(r.get("text") or fb_rewrites[style]["text"]),
            "emoji": str(r.get("emoji") or STYLE_EMOJI[style]),
        })

    nm = data.get("nextMessages")
    if isinstance(nm, list) and nm:
        out["nextMessages"] = [
            {"scenario": str(x.get("scenario", "")), "suggestion": str(x.get("suggestion", ""))}
            for x in nm if isinstance(x, dict)
        ][:3]
    else:
        out["nextMessages"] = fallback["nextMessages"]

    out["mode"] = data.get("mode") if data.get("mode") in ("normal", "new_person", "argument") else fallback["mode"]
    ma = data.get("modeAdvice", fallback["modeAdvice"])
    out["modeAdvice"] = str(ma) if ma else None
    out["engine"] = data.get("engine", "gemini")
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@analyze_api.route('/analyze', methods=['POST', 'OPTIONS'])
def analyze():
    if request.method == 'OPTIONS':
        return ('', 204)

    body = request.get_json(silent=True) or {}
    message = body.get('message')
    context = body.get('context') or 'friend'
    tone_bias = body.get('toneBias')

    # --- Error handling: empty / invalid input ---
    if not message or not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Message is required"}), 400
    if len(message) > 2000:
        return jsonify({"error": "Message is too long (2000 char max)."}), 400

    message = message.strip()

    # Heuristic is always computed — it's the guaranteed-valid fallback.
    fallback = _heuristic(message, context, tone_bias)

    ai = _analyze_with_gemini(message, context, tone_bias)
    result = _normalize(ai, fallback) if ai is not None else fallback

    # Persist (best-effort — never let a DB error break the response).
    try:
        from model.analysis import Analysis
        Analysis(message=message, context=context, result=result, tone_bias=tone_bias).create()
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to persist analysis: %s", e)

    return jsonify(result), 200


@analyze_api.route('/analyze/history', methods=['GET'])
def history():
    """Return the most recent analyses stored on the server (SQLite)."""
    try:
        from model.analysis import Analysis
        limit = min(int(request.args.get('limit', 50)), 200)
        rows = (Analysis.query.order_by(Analysis.id.desc()).limit(limit).all())
        return jsonify([r.read() for r in rows]), 200
    except Exception as e:  # noqa: BLE001
        logger.warning("history read failed: %s", e)
        return jsonify([]), 200


@analyze_api.route('/analyze/history', methods=['DELETE'])
def clear_history():
    """Wipe server-side history (used by the Privacy 'delete my data' flow)."""
    try:
        from model.analysis import Analysis
        from __init__ import db
        Analysis.query.delete()
        db.session.commit()
        return jsonify({"message": "History cleared"}), 200
    except Exception as e:  # noqa: BLE001
        logger.warning("history clear failed: %s", e)
        return jsonify({"error": "Could not clear history"}), 500


@analyze_api.route('/analyze/health', methods=['GET'])
def health():
    """Lightweight health check the frontend/Make can ping."""
    return jsonify({
        "status": "ok",
        "service": "textback-analyze",
        "engine": "gemini" if _gemini_key() else "heuristic",
    }), 200
