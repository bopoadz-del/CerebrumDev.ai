from typing import List, Dict


def parse_rules(rule_texts: List[str]) -> List[Dict[str, str]]:
    """Convert free-text rules into structured rule objects."""
    parsed = []
    for text in rule_texts:
        text = text.strip()
        if not text:
            continue
        parsed.append({
            "raw": text,
            "trigger": _extract_trigger(text),
            "action": _extract_action(text),
            "code_snippet": _generate_snippet(text),
        })
    return parsed


def _extract_trigger(text: str) -> str:
    """Naive trigger extraction."""
    lower = text.lower()
    if "if " in lower:
        return text.split("if ", 1)[1].split(" then", 1)[0].strip(" .")
    return "always"


def _extract_action(text: str) -> str:
    """Naive action extraction."""
    lower = text.lower()
    if " then " in lower:
        return text.split(" then ", 1)[1].strip(" .")
    if "always " in lower:
        return text.lower().replace("always ", "").strip(" .")
    return text


def _generate_snippet(text: str) -> str:
    """Generate a placeholder Python snippet for the rule."""
    trigger = _extract_trigger(text)
    action = _extract_action(text)
    return (
        f"# Rule: {text}\n"
        f"if context.matches({trigger!r}):\n"
        f"    context.apply_action({action!r})\n"
    )
