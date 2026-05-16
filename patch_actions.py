import pathlib

path = pathlib.Path(r"c:\Users\aacer\Documents\Anvil\autopilot\src\autopilot\connectors\actions.py")
raw = path.read_bytes().decode("utf-8")
# Normalise to LF for easier matching, then re-apply CRLF at the end
text = raw.replace("\r\n", "\n")

# ── Patch 1: add channel_env + channel_warning ────────────────────────────
old1 = (
    "        has_token = bool(os.getenv(\"SLACK_ACCESS_TOKEN\"))\n"
    "        has_webhook = bool(os.getenv(\"SLACK_WEBHOOK_URL\"))\n"
    "        \n"
    "        mode = "
)
new1 = (
    "        has_token = bool(os.getenv(\"SLACK_ACCESS_TOKEN\"))\n"
    "        has_webhook = bool(os.getenv(\"SLACK_WEBHOOK_URL\"))\n"
    "        channel_env = os.getenv(\"SLACK_DEFAULT_CHANNEL\", \"\").strip()\n"
    "        channel_warning = None\n"
    "\n"
    "        if has_token and not channel_env:\n"
    "            channel_warning = (\n"
    "                \"SLACK_DEFAULT_CHANNEL is not set. Defaulting to '#ops'. \"\n"
    "                \"Set SLACK_DEFAULT_CHANNEL to your workspace channel name.\"\n"
    "            )\n"
    "\n"
    "        mode = "
)
assert old1 in text, f"Patch 1 not found. Context:\n{text[text.find('has_token'):text.find('has_token')+200]}"
text = text.replace(old1, new1, 1)

# ── Patch 2: add channel_warning key to return dict ───────────────────────
old2 = (
    "            \"integration_live\": mode != \"local_fallback\",\n"
    "        }\n"
    "\n"
    "    async def action"
)
new2 = (
    "            \"integration_live\": mode != \"local_fallback\",\n"
    "            \"channel_warning\": channel_warning,\n"
    "        }\n"
    "\n"
    "    async def action"
)
assert old2 in text, "Patch 2 not found"
text = text.replace(old2, new2, 1)

# ── Patch 3: channel_not_found detection in Slack action ──────────────────
old3 = (
    "                    if not data.get(\"ok\"):\n"
    "                        raise Exception(f\"Slack API error: {data.get('error')}\")"
)
new3 = (
    "                    if not data.get(\"ok\"):\n"
    "                        slack_err = data.get(\"error\", \"unknown_error\")\n"
    "                        if slack_err == \"channel_not_found\":\n"
    "                            log.error(\n"
    "                                \"Slack channel_not_found: channel does not exist. \"\n"
    "                                \"Set SLACK_DEFAULT_CHANNEL to a valid channel name.\"\n"
    "                            )\n"
    "                        else:\n"
    "                            log.error(\"Slack API error: %s\", slack_err)\n"
    "                        raise Exception(f\"Slack API error: {slack_err}\")"
)
assert old3 in text, "Patch 3 not found"
text = text.replace(old3, new3, 1)

# Restore CRLF line endings (file was CRLF originally)
text = text.replace("\n", "\r\n")
path.write_bytes(text.encode("utf-8"))
print("Done. Applied 3 patches.")

result = path.read_bytes().decode("utf-8")
assert "channel_warning" in result, "channel_warning missing"
assert "channel_not_found" in result, "channel_not_found missing"
print("Verification passed.")
