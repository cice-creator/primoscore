import json


TOTAL_STEPS = 7

QUESTION_RULES = {
    1: {
        "creditHistory": {"type": "choice", "options": {"none", "past", "active"}},
    },
    2: {
        "applicantAge": {"type": "integer", "min": 18, "max": 80},
        "monthlyIncome": {"type": "integer", "min": 500, "max": 30000},
        "annualPayments": {"type": "integer", "min": 12, "max": 14},
        "jobType": {
            "type": "choice",
            "options": {"permanent", "self", "apprentice", "fixed", "trial", "pension"},
        },
    },
    3: {
        "householdSize": {"type": "integer", "min": 1, "max": 12},
        "children": {"type": "integer", "min": 0, "max": 10},
        "householdEarners": {"type": "integer", "min": 1, "max": 5},
        "istatRegion": {"type": "integer", "min": 1, "max": 20, "conditional": True},
        "istatMunicipalityType": {"type": "choice", "options": {"1", "2", "3"}, "conditional": True},
        "istatAge0to3": {"type": "integer", "min": 0, "max": 12, "conditional": True},
        "istatAge4to10": {"type": "integer", "min": 0, "max": 12, "conditional": True},
        "istatAge11to17": {"type": "integer", "min": 0, "max": 12, "conditional": True},
        "istatAge18to29": {"type": "integer", "min": 0, "max": 12, "conditional": True},
        "istatAge30to59": {"type": "integer", "min": 0, "max": 12, "conditional": True},
        "istatAge60to74": {"type": "integer", "min": 0, "max": 12, "conditional": True},
        "istatAge75plus": {"type": "integer", "min": 0, "max": 12, "conditional": True},
    },
    4: {
        "savings": {"type": "integer", "min": 0, "max": 2000000},
        "monthlyDebts": {"type": "integer", "min": 0, "max": 20000},
        "savingHabit": {"type": "choice", "options": {"yes", "some", "no"}},
    },
    5: {
        "propertyPrice": {"type": "integer", "min": 10000, "max": 5000000},
        "loanAmount": {"type": "integer", "min": 5000, "max": 2000000},
        "loanTerm": {"type": "integer", "min": 5, "max": 40},
        "purpose": {
            "type": "choice",
            "options": {"first_home", "second_home", "renovation", "surrogation"},
        },
        "otherHome": {"type": "choice", "options": {"no", "exception", "yes"}},
    },
    6: {
        "supportRole": {
            "type": "choice",
            "options": {"none", "coapplicant", "guarantor"},
        },
        "supportAge": {"type": "integer", "min": 18, "max": 85, "conditional": True},
        "supportIncome": {"type": "integer", "min": 0, "max": 30000, "conditional": True},
    },
    7: {
        "iseeBand": {"type": "choice", "options": {"under_or_equal_40k", "over_40k"}},
    },
}

QUESTION_ROLES = {
    "supportAge": "support",
    "supportIncome": "support",
}


def validate_step(step, values):
    rules = QUESTION_RULES.get(step)
    if not rules:
        return {}, {"step": "Fase non valida."}
    cleaned = {}
    errors = {}
    support_role = values.get("supportRole")
    for question_id, rule in rules.items():
        required = not rule.get("conditional")
        if rule.get("conditional"):
            required = support_role in {"coapplicant", "guarantor"}
        raw_value = values.get(question_id)
        if raw_value in (None, ""):
            if required:
                errors[question_id] = "Risposta obbligatoria."
            continue
        if rule["type"] == "choice":
            if raw_value not in rule["options"]:
                errors[question_id] = "Seleziona una risposta valida."
            else:
                cleaned[question_id] = raw_value
        elif rule["type"] == "integer":
            try:
                number = int(raw_value)
            except (TypeError, ValueError):
                errors[question_id] = "Inserisci un numero valido."
                continue
            if number < rule["min"] or number > rule["max"]:
                errors[question_id] = f"Inserisci un valore tra {rule['min']} e {rule['max']}."
            else:
                cleaned[question_id] = number
    return cleaned, errors


def validate_partial_step(step, values):
    rules = QUESTION_RULES.get(step)
    if not rules:
        return {}, {"step": "Fase non valida."}
    present = {key: value for key, value in values.items() if key in rules and value not in (None, "")}
    cleaned = {}
    errors = {}
    for question_id, raw_value in present.items():
        rule = rules[question_id]
        if rule["type"] == "choice":
            if raw_value not in rule["options"]:
                errors[question_id] = "Seleziona una risposta valida."
            else:
                cleaned[question_id] = raw_value
        else:
            try:
                number = int(raw_value)
            except (TypeError, ValueError):
                errors[question_id] = "Inserisci un numero valido."
                continue
            if number < rule["min"] or number > rule["max"]:
                errors[question_id] = f"Inserisci un valore tra {rule['min']} e {rule['max']}."
            else:
                cleaned[question_id] = number
    return cleaned, errors


def required_question_ids(answers):
    required = set()
    for step, rules in QUESTION_RULES.items():
        if step == 7:
            continue
        for question_id, rule in rules.items():
            if not rule.get("conditional"):
                required.add(question_id)
    applicant_under_36 = 0 < int(answers.get("applicantAge") or 0) < 36
    coapplicant_under_36 = (
        answers.get("supportRole") == "coapplicant"
        and 0 < int(answers.get("supportAge") or 0) < 36
    )
    if applicant_under_36 or coapplicant_under_36:
        required.add("iseeBand")
    if answers.get("supportRole") in {"coapplicant", "guarantor"}:
        required.update({"supportAge", "supportIncome"})
    return required


def validate_complete_answers(answers):
    missing = sorted(required_question_ids(answers) - set(answers))
    return missing


def role_for(question_id, answers):
    if question_id not in QUESTION_ROLES:
        return "applicant"
    return answers.get("supportRole", "support")


def decode_answers(rows):
    return {row["question_id"]: json.loads(row["value_json"]) for row in rows}
