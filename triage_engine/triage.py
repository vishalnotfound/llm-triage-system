"""Apply profile rules in order and provide a support keyword smoke check."""

from .profile import load_profile


def validate_signals(signals, profile):
    if not isinstance(signals, dict) or set(signals) != set(profile.descriptions):
        raise ValueError("Expected exactly the configured policy signals")
    if any(type(value) is not bool for value in signals.values()):
        raise ValueError("Policy signals must be booleans")


def apply_policy(ticket_id, signals, rules, fallback):
    route, action, priority = next(
        (rule[1:] for rule in rules if signals[rule[0]]), fallback
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


def decide(ticket, profile=None):
    signals = keyword_signals(ticket)
    profile = profile or load_profile()
    if set(signals) != set(profile.descriptions):
        raise ValueError("The keyword smoke check requires the original support signals")
    return apply_policy(ticket["id"], signals, profile.rules, profile.fallback), {"signals": signals}
