from __future__ import annotations

from typing import Protocol

QUESTION_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "question",
        "description": (
            "Ask the user one or more questions to gather preferences, "
            "clarify ambiguity, or make an implementation decision. Use "
            "only when multiple materially different approaches are "
            "possible and the user's preference is the deciding factor."),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "header": {"type": "string"},
                            "multiple": {"type": "boolean", "default": False},
                            "options": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label", "description"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["question", "header", "options"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["questions"],
            "additionalProperties": False,
        },
    },
}


class Questioner(Protocol):
    def ask(self, questions: list[dict]) -> list[tuple[str, str]] | None:
        """Renders the question batch, blocks for input. Returns a flat
        list of (header, selected_text) pairs, or None if the whole
        batch was rejected."""
        ...


def validate_shape(arguments: dict) -> list[dict] | None:
    if not isinstance(arguments, dict):
        return None
    questions = arguments.get("questions")
    if not isinstance(questions, list) or len(questions) == 0:
        return None
    result = []
    for q in questions:
        if not isinstance(q, dict):
            return None
        question_text = q.get("question")
        header = q.get("header")
        options = q.get("options")
        multiple = q.get("multiple", False)
        if not isinstance(question_text, str) or not question_text:
            return None
        if not isinstance(header, str) or not header:
            return None
        if not isinstance(multiple, bool):
            return None
        if not isinstance(options, list) or len(options) == 0:
            return None
        validated_options = []
        for opt in options:
            if not isinstance(opt, dict):
                return None
            label = opt.get("label")
            description = opt.get("description")
            if not isinstance(label, str) or not label:
                return None
            if not isinstance(description, str) or not description:
                return None
            validated_options.append({"label": label, "description": description})
        result.append({"question": question_text, "header": header,
                        "multiple": multiple, "options": validated_options})
    return result


def format_result(pairs: list[tuple[str, str]]) -> str:
    answered = ", ".join(f'"{header}"="{answer}"' for header, answer in pairs)
    return (f"User has answered your questions:\n{answered}\n\n"
            f"You can now continue with the user's answers in mind.")
