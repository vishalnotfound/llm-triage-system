"""Deterministic policy layer, plus an offline keyword heuristic.

The precedence from policy.md lives here once. `--mode api` asks a model only
for the signals below and applies these rules; `--mode heuristic` guesses the
signals from keywords as an offline smoke check.
"""

# (signal, route, action, priority) in policy.md order; the first match wins.
RULES = (
    ("safety_incident", "safety", "escalate", "urgent"),  # rule 1
    ("third_party_data_request", "privacy", "refuse", "normal"),  # rule 2
    ("privacy_rights_request", "privacy", "verify_identity", "normal"),  # rule 3
    ("privacy_policy_question", "privacy", "reply", "normal"),  # rule 3
    ("account_change_request", "access", "verify_identity", "normal"),  # rule 4
    ("sign_in_troubleshooting", "access", "reply", "normal"),  # rule 4
    ("billing_question", "billing", "reply", "normal"),  # rule 5
)
FALLBACK = ("general", "reply", "normal")  # rule 6
SIGNALS = tuple(rule[0] for rule in RULES)


def apply_policy(ticket_id, signals, rules=RULES):
    route, action, priority = next(
        (rule[1:] for rule in rules if signals[rule[0]]), FALLBACK
    )
    return {
        "id": ticket_id,
        "route": route,
        "action": action,
        "priority": priority,
        "escalate": action == "escalate",
    }


# Keywords cannot tell a quoted or hypothetical incident from a real one, or
# notice instructions embedded in a ticket. Smoke check only.
KEYWORDS = {
    "safety_incident": ("leaked", "compromised", "hacked", "not me", "stolen"),
    "third_party_data_request": ("coworker", "colleague", "someone else", "another user"),
    "privacy_rights_request": ("delete", "erase", "export", "copy of", "correct my"),
    "privacy_policy_question": ("privacy policy", "retention"),
    "account_change_request": ("reset", "change my email", "mfa", "two-factor", "unlock"),
    "sign_in_troubleshooting": ("sign-in", "sign in", "login", "log in"),
    "billing_question": ("invoice", "charge", "refund", "price", "cost", "plan", "billing"),
}


def keyword_signals(ticket):
    text = (ticket["subject"] + " " + ticket["body"]).lower()
    return {signal: any(word in text for word in words) for signal, words in KEYWORDS.items()}


def decide(ticket):
    signals = keyword_signals(ticket)
    return apply_policy(ticket["id"], signals), {"signals": signals}
