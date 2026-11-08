"""Apply profile rules in precedence order."""

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
