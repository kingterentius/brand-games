"""Intake receiver for the brand workshop games.

Goal: save each player's card to Odoo CRM so Terence can write a need-based proposal.
For: Terence Latimer and anyone maintaining TLE infrastructure.

GET  ?for=CODE  -> {"first": "Zulu"} when CODE matches an open opportunity (first name only, never contact details)
POST JSON       -> with a valid code: note + follow-up task on that opportunity
                   without one: a new Latimer Digital lead built from the contact details the player typed
"""
import html
import json
import os
import re
import xmlrpc.client

import functions_framework

ALLOWED = {
    "https://brand.terencelatimer.com",
    "https://voice.terencelatimer.com",
    "https://terencelatimer-brand.web.app",
    "https://terencelatimer-voice.web.app",
}
TEAM_ID = 12        # Latimer Digital
OWNER_ID = 2        # Terence
STAGE_QUALIFIED = 2
EARLY_STAGES = {1, 12}  # New, Contacted
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
GAMES = {"brand": "See Your Brand", "voice": "Tune Your Voice"}


def _odoo():
    env = lambda k: os.environ[k].strip()
    url = env("ODOO_URL").rstrip("/")
    db, user, key = env("ODOO_DB"), env("ODOO_USER"), env("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, user, key, {})
    if not uid:
        raise RuntimeError("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)

    def call(model, method, *args, **kw):
        return models.execute_kw(db, uid, key, model, method, list(args), kw)

    return call


def _headers(origin):
    h = {"Vary": "Origin"}
    if origin in ALLOWED:
        h.update({
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        })
    return h


def _reply(body, status, origin):
    h = _headers(origin)
    h["Content-Type"] = "application/json"
    return (json.dumps(body), status, h)


def _clean(value, limit=500):
    return str(value or "").strip()[:limit]


def _find_by_code(call, code):
    if not CODE_RE.match(code or ""):
        return None
    rows = call("crm.lead", "search_read", [["x_intake_code", "=", code], ["active", "=", True]],
                fields=["id", "partner_id", "contact_name", "stage_id"], limit=1)
    return rows[0] if rows else None


def _note(call, lead_id, body):
    # Writing mail.message directly keeps the HTML table; message_post over RPC escapes it.
    call("mail.message", "create", {"model": "crm.lead", "res_id": lead_id, "body": body,
                                    "message_type": "comment", "subtype_id": 2})


def _card_html(game, card, contact=None):
    title = GAMES.get(game, "Workshop")
    rows = "".join(
        f"<tr><td><b>{html.escape(_clean(pair[0], 80))}</b></td><td>{html.escape(_clean(pair[1], 1200))}</td></tr>"
        for pair in card[:30] if isinstance(pair, (list, tuple)) and len(pair) == 2
    )
    who = ""
    if contact:
        who = "<p>" + " · ".join(html.escape(_clean(contact.get(f), 200)) for f in
                                 ("name", "email", "phone", "business", "website") if contact.get(f)) + "</p>"
    return f"<p><b>{html.escape(title)} card received</b></p>{who}<table>{rows}</table>"


@functions_framework.http
def intake(request):
    origin = request.headers.get("Origin", "")
    if request.method == "OPTIONS":
        return ("", 204, _headers(origin))
    if origin not in ALLOWED:
        return _reply({"error": "origin"}, 403, origin)

    if request.method == "GET":
        try:
            lead = _find_by_code(_odoo(), request.args.get("for", ""))
        except Exception as exc:
            print(f"lookup error: {exc!r}")
            return _reply({"error": "lookup"}, 502, origin)
        if not lead:
            return _reply({}, 404, origin)
        name = lead.get("contact_name") or (lead["partner_id"][1] if lead.get("partner_id") else "")
        return _reply({"first": name.split()[0] if name else ""}, 200, origin)

    if request.method != "POST":
        return _reply({"error": "method"}, 405, origin)
    if (request.content_length or 0) > 40000:
        return _reply({"error": "size"}, 413, origin)
    data = request.get_json(silent=True) or {}
    if _clean(data.get("hp")):          # honeypot field filled in: a bot
        return _reply({"ok": True}, 200, origin)
    game = data.get("game") if data.get("game") in GAMES else "brand"
    card = data.get("card") if isinstance(data.get("card"), list) else []

    try:
        call = _odoo()
        lead = _find_by_code(call, data.get("code"))
        if lead:
            _note(call, lead["id"], _card_html(game, card))
            if lead["stage_id"] and lead["stage_id"][0] in EARLY_STAGES:
                call("crm.lead", "write", [lead["id"]], {"stage_id": STAGE_QUALIFIED})
            call("crm.lead", "activity_schedule", [lead["id"]], act_type_xmlid="mail.mail_activity_data_todo",
                 summary=f"Review {GAMES[game]} intake and draft proposal", user_id=OWNER_ID)
            return _reply({"ok": True, "matched": True}, 200, origin)

        c = data.get("contact") or {}
        name, email = _clean(c.get("name"), 120), _clean(c.get("email"), 200).lower()
        if not name or not EMAIL_RE.match(email) or not c.get("consent"):
            return _reply({"error": "contact"}, 400, origin)
        title = f"[LD] Brand & Voice Intake — {name}"
        existing = call("crm.lead", "search", [["email_from", "=ilike", email], ["name", "=", title], ["active", "=", True]], limit=1)
        if existing:
            lead_id = existing[0]
        else:
            lead_id = call("crm.lead", "create", {
                "name": title, "type": "lead", "team_id": TEAM_ID, "user_id": OWNER_ID,
                "contact_name": name, "email_from": email, "phone": _clean(c.get("phone"), 40),
                "partner_name": _clean(c.get("business"), 160), "website": _clean(c.get("website"), 200),
                "description": "<p>Came in through the public brand workshop (no personal link). "
                               "The player agreed to be contacted about their brand.</p>",
            })
        if isinstance(lead_id, list):
            lead_id = lead_id[0]
        _note(call, lead_id, _card_html(game, card, c))
        call("crm.lead", "activity_schedule", [lead_id], act_type_xmlid="mail.mail_activity_data_todo",
             summary=f"New {GAMES[game]} intake from {name}", user_id=OWNER_ID)
        return _reply({"ok": True, "matched": False}, 200, origin)
    except Exception as exc:  # keep details in the logs, not the response
        print(f"intake error: {exc!r}")
        return _reply({"error": "save"}, 502, origin)
