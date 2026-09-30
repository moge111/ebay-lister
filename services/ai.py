"""Claude calls for grouping and identifying photos.

Two backends:
- "api": Anthropic SDK with an API key (fastest; runs many items in parallel).
- "cli": the local `claude` CLI in headless mode, riding the Claude Code
  subscription. Images are streamed straight into the prompt (no tool calls),
  with MCP servers, skills, settings and project memory disabled so each call
  starts fast.
"""
import json
import os
import shutil
import subprocess
import tempfile

import settings


class AIError(RuntimeError):
    pass


class AIAuthError(AIError):
    """The backend can't authenticate — every further call would fail too."""


def backend():
    choice = settings.get("ai_backend")
    has_key = bool(settings.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"))
    if choice == "api" or (choice == "auto" and has_key):
        return "api" if has_key else None
    if shutil.which("claude"):
        return "cli"
    return None


def ask_json(blocks, schema, system, max_tokens=4096):
    """blocks: [{"image": b64jpeg} | {"text": str}] -> dict matching schema."""
    which = backend()
    if which == "api":
        return _ask_api(blocks, schema, system, max_tokens)
    if which == "cli":
        return _ask_cli(blocks, schema, system)
    raise AIAuthError("No AI backend: add an Anthropic API key in Settings, or install and log in to the `claude` CLI.")


def _content(blocks):
    content = []
    for b in blocks:
        if "image" in b:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b["image"]}})
        else:
            content.append({"type": "text", "text": b["text"]})
    return content


def _ask_api(blocks, schema, system, max_tokens):
    import anthropic
    client = anthropic.Anthropic(api_key=settings.get("anthropic_api_key") or None, timeout=300)
    try:
        response = client.beta.messages.create(
            model=settings.get("ai_model"),
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"format": {"type": "json_schema", "schema": schema}, "effort": "medium"},
            messages=[{"role": "user", "content": _content(blocks)}],
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},  # re-run on a fallback model if refused
        )
    except anthropic.AuthenticationError as e:
        raise AIAuthError(f"Anthropic API key rejected: {e.message}") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Anthropic API {e.status_code}: {e.message}") from e
    if response.stop_reason == "refusal":
        raise AIError("model declined the request")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


def _ask_cli(blocks, schema, system):
    message = {"type": "user", "message": {"role": "user", "content": _content(blocks)}}
    # Scrub inherited session vars so the nested CLI uses its own stored login
    # (matters when this app was itself started from a Claude Code session).
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("ANTHROPIC") or k.startswith("CLAUDE") or k in ("AI_AGENT", "BAGGAGE"))}
    cmd = [
        "claude", "-p",
        "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--model", settings.get("ai_cli_model") or "opus",
        "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", "",
        "--system-prompt", system,
        "--json-schema", json.dumps(schema),
    ]
    try:
        result = subprocess.run(
            cmd, input=json.dumps(message) + "\n", capture_output=True, text=True,
            timeout=420, cwd=tempfile.gettempdir(), env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise AIError("claude CLI timed out after 7 minutes") from e

    final = None
    for line in result.stdout.splitlines():
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if evt.get("type") == "result":
            final = evt
    if final is None:
        raise AIError(f"claude CLI gave no result: {(result.stderr or result.stdout)[-300:]}")
    text = final.get("result") or ""
    if final.get("is_error"):
        if "authenticat" in text.lower() or "401" in text or "login" in text.lower():
            raise AIAuthError(
                "The `claude` CLI isn't logged in (token expired). Run `claude` in a terminal and "
                "use /login, or add an Anthropic API key in Settings."
            )
        raise AIError(f"claude CLI error: {text[:300]}")
    if isinstance(final.get("structured_output"), dict):
        return final["structured_output"]
    return _parse_json(text)


def _parse_json(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AIError(f"no JSON in reply: {text[:200]}")
    return json.loads(text[start:end + 1])


def health():
    """Quick end-to-end check used by the Settings page."""
    which = backend()
    if not which:
        return {"ok": False, "backend": None, "message": "No backend configured"}
    try:
        out = ask_json(
            [{"text": "Reply with ok=true."}],
            {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
            "You are a health check.", max_tokens=200,
        )
        return {"ok": bool(out.get("ok")), "backend": which, "message": "Working"}
    except AIError as e:
        return {"ok": False, "backend": which, "message": str(e)}
