"""Motore MutuoScore: tutte le soglie e i punteggi vivono nella Costituzione JSON."""

import json
import math
from urllib.parse import urlencode
from urllib.request import urlopen
from pathlib import Path

CONSTITUTION_PATH = Path(__file__).with_name("mutuoscore_constitution.json")


def load_constitution():
    """Rilegge il documento autorevole a ogni calcolo."""
    try:
        with CONSTITUTION_PATH.open(encoding="utf-8") as file:
            rules = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Costituzione MutuoScore non leggibile o non valida.") from error
    required = {"version", "calculation", "decision_thresholds", "areas", "caps_and_messages", "classification", "consap", "istat_sussistenza"}
    if not required.issubset(rules):
        raise RuntimeError("Costituzione MutuoScore incompleta.")
    return rules


# Compatibilita per codice esistente; ogni nuovo calcolo usa comunque la versione corrente.
ENGINE_VERSION = load_constitution()["version"]


def get_engine_version():
    return load_constitution()["version"]


def calculate_score(answers):
    rules = load_constitution()
    assumptions = rules["calculation"]["assumptions"]
    income = number(answers.get("monthlyIncome"))
    support_role = answers.get("supportRole", "none")
    support_income = number(answers.get("supportIncome"))
    total_income = income + (support_income if support_role == "coapplicant" else 0)
    debts, savings = number(answers.get("monthlyDebts")), number(answers.get("savings"))
    price, loan = number(answers.get("propertyPrice")), number(answers.get("loanAmount"))
    term = max(assumptions["minimum_term_years_for_calculation"], number(answers.get("loanTerm")))
    children, earners = number(answers.get("children")), number(answers.get("householdEarners"))
    household = max(1, number(answers.get("householdSize")))
    ltv = loan / price * 100 if price else assumptions["default_ltv_if_price_missing"]
    payment = amortized_payment(loan, term, assumptions["annual_interest_rate"])
    commitment = (debts + payment) / total_income * 100 if total_income else assumptions["default_commitment_ratio_if_income_missing"]
    areas = {
        "affidabilita_creditizia": mapped_score(rules["areas"]["affidabilita_creditizia"], answers.get("creditHistory")),
        "solidita_reddituale": bracket_score(total_income, rules["areas"]["solidita_reddituale"]),
        "solidita_lavorativa": mapped_score(rules["areas"]["solidita_lavorativa"], answers.get("jobType")),
        "situazione_familiare": family_score(rules["areas"]["situazione_familiare"], household, children, earners),
        "capacita_risparmio": savings_score(rules["areas"]["capacita_risparmio"], savings, price),
        "comportamento_finanziario": behavior_score(rules["areas"]["comportamento_finanziario"], answers.get("savingHabit"), commitment, debts, total_income),
        "valutazione_operazione": operation_score(rules["areas"]["valutazione_operazione"], ltv, commitment, answers.get("purpose")),
        "garanzie_supporti": support_score(rules["areas"]["garanzie_supporti"], support_role, support_income, number(answers.get("supportAge"))),
    }
    areas = {key: clamp(value) for key, value in areas.items()}
    raw_score = round(sum(areas.values()) / len(areas))
    caps, warnings, strengths = outcome_messages(rules, answers, savings, price, ltv, commitment, support_role)
    subsistence = calculate_subsistence(rules["istat_sussistenza"], answers, total_income, debts, payment)
    if subsistence["status"] == "below_threshold":
        warnings.append("Dopo rata stimata e impegni, il reddito del nucleo risulta sotto la soglia ISTAT di sussistenza.")
    elif subsistence["status"] == "attention":
        warnings.append("Dopo rata stimata e impegni, il margine rispetto alla soglia ISTAT di sussistenza è contenuto.")
    elif subsistence["status"] == "unavailable":
        warnings.append(subsistence["message"])
    final_score = clamp(min([raw_score] + caps) if caps else raw_score)
    indicative_payment = calculate_indicative_payment(rules["rata_indicativa"], loan, term)
    return {"engineVersion": rules["version"], "totalScore": final_score, "rawScore": raw_score,
            "classification": classify(rules["classification"], final_score), "areaScores": areas,
            "strengths": unique(strengths)[:5], "warnings": unique(warnings)[:6],
            "metrics": {"ltv": round(ltv, 1), "estimatedMonthlyPayment": round(payment), "commitmentRatio": round(commitment, 1), "totalHouseholdIncome": round(total_income), "subsistence": subsistence, "indicativePayment": indicative_payment},
            "consap": evaluate_consap(rules["consap"], answers, ltv)}


def calculate_indicative_payment(rule, loan, term):
    rate = rule["annual_interest_rate"]
    value = amortized_payment(loan, term, rate)
    notice = rule["customer_notice"].replace("{rate}", f"{rate * 100:.1f}%".replace(".", ","))
    return {"monthlyAmount": round(value), "annualInterestRate": rate, "notice": notice}


def calculate_subsistence(rule, answers, total_income, debts, payment):
    """Legge solo dati aggregati dal calcolatore ISTAT; il punteggio non viene modificato."""
    keys = ("istatAge0to3", "istatAge4to10", "istatAge11to17", "istatAge18to29", "istatAge30to59", "istatAge60to74", "istatAge75plus")
    counts = [number(answers.get(key)) for key in keys]
    household = number(answers.get("householdSize"))
    if not rule.get("enabled") or not household or sum(counts) != household or sum(counts[3:]) < 1:
        return {"status": "unavailable", "message": rule["failure_message"], "referenceYear": rule.get("reference_year")}
    params = {"tipo_select": "calcola", "params[anno]": rule["reference_year"], "params[regione]": number(answers.get("istatRegion")), "params[tipologia]": answers.get("istatMunicipalityType")}
    params.update({f"params[eta{i + 1}]": count for i, count in enumerate(counts)})
    try:
        with urlopen(f"{rule['lookup_url']}?{urlencode(params)}", timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
        value = payload["elements"][0]["SOGLIA"]
        threshold = float(str(value).replace(".", "").replace(",", ".")) if "," in str(value) else float(value)
    except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError):
        return {"status": "unavailable", "message": rule["failure_message"], "referenceYear": rule.get("reference_year")}
    post_commitment = total_income - debts - payment
    ratio = post_commitment / threshold * 100 if threshold else 0
    thresholds = rule["status_thresholds"]
    status = "adequate" if ratio >= thresholds["adequate_ratio_min"] else "attention" if ratio >= thresholds["attention_ratio_min"] else "below_threshold"
    return {"status": status, "referenceYear": rule["reference_year"], "source": rule["source"], "threshold": round(threshold), "postCommitmentIncome": round(post_commitment), "margin": round(post_commitment - threshold), "ratio": round(ratio, 1)}


def outcome_messages(rules, answers, savings, price, ltv, commitment, support_role):
    caps, warnings, strengths = [], [], []
    thresholds = rules["decision_thresholds"]
    for rule in rules["caps_and_messages"]:
        condition = rule["when"]
        applies = ((condition == "creditHistory = active" and answers.get("creditHistory") == "active") or
                   (condition == "creditHistory = past" and answers.get("creditHistory") == "past") or
                   (condition == "savings < 5000" and savings < thresholds["low_liquidity"]) or
                   (condition == "jobType = trial" and answers.get("jobType") == "trial") or
                   (condition == "jobType in fixed, apprentice" and answers.get("jobType") in {"fixed", "apprentice"}) or
                   (condition == "commitment_ratio > 40" and commitment > thresholds["high_commitment_ratio"]) or
                   (condition == "ltv > 95" and ltv > thresholds["very_high_ltv"]))
        if applies:
            cap = rule.get("cap")
            if isinstance(cap, dict): caps.append(cap["with_support"] if support_role in {"coapplicant", "guarantor"} else cap["without_support"])
            elif cap is not None: caps.append(cap)
            if "warning" in rule: warnings.append(rule["warning"])
    for rule in rules.get("strengths", []):
        condition = rule["when"]
        applies = ((condition == "creditHistory = none" and answers.get("creditHistory") == "none") or
                   (condition == "savings >= propertyPrice * 0.2" and savings >= price * thresholds["strength_savings_ratio"]) or
                   (condition == "jobType in permanent, pension" and answers.get("jobType") in {"permanent", "pension"}) or
                   (condition == "commitment_ratio <= 30" and commitment <= thresholds["favourable_commitment_ratio"]) or
                   (condition == "ltv <= 80" and ltv <= thresholds["favourable_ltv"]))
        if applies: strengths.append(rule["message"])
    return caps, warnings, strengths


def evaluate_consap(rules, answers, ltv):
    blocking, block, messages = [], rules["blocking"], rules["messages"]
    category = rules["categories"]
    applicant_under_36 = 0 < number(answers.get("applicantAge")) < category["applicant_under_age"]
    coapplicant_under_36 = (
        answers.get("supportRole") == "coapplicant"
        and 0 < number(answers.get("supportAge")) < category["applicant_under_age"]
    )
    if not applicant_under_36 and not coapplicant_under_36:
        return consap_result(False, "none", [], [messages["over_age"]], messages["over_age"])
    if answers.get("purpose") != "first_home": blocking.append(block["purpose_not_first_home"])
    if number(answers.get("loanAmount")) > block["loan_amount_max"]: blocking.append(block["loan_amount_message"])
    if answers.get("otherHome") == "yes": blocking.append(block["other_home_yes"])
    if blocking: return consap_result(False, "none", [], blocking, messages["blocked"])
    categories = []
    if applicant_under_36:
        categories.append(category["applicant"])
    if coapplicant_under_36:
        categories.append(category["coapplicant"])
    guarantee = rules["guarantees"]
    elevated = ltv > guarantee["elevated_ltv_over"] and answers.get("iseeBand") == guarantee["elevated_isee_required"]
    return consap_result(True, guarantee["elevated"] if elevated else guarantee["ordinary"], categories, [], messages["possible"])


def consap_result(eligible, guarantee, categories, blocking, explanation):
    return {"accessStatus": "eligible_possible" if eligible else "not_eligible_blocking_rule", "customerPromptAllowed": eligible, "guaranteeType": guarantee, "eligibleCategories": categories, "blockingReasons": blocking, "explanation": explanation}

def mapped_score(rule, value): return rule["scores"].get(value, rule["default"])
def bracket_score(value, rule):
    for minimum, score in rule["brackets"]:
        if value >= minimum: return score
    return rule["default"]
def family_score(rule, household, children, earners):
    score = rule["multi_earner_score"] if earners >= 2 else rule["no_children_score"] if children == 0 else max(rule["minimum"], rule["children_base"] - children * rule["children_penalty"])
    return score - rule["large_single_income"]["penalty"] if household >= rule["large_single_income"]["household_min"] and earners == 1 else score
def savings_score(rule, savings, price):
    if price <= 0: return rule["no_price_score"]
    for minimum, score in rule["ratio_brackets"]:
        if savings / price >= minimum: return score
    for minimum, score in rule["amount_brackets"]:
        if savings >= minimum: return score
    return rule["default"]
def behavior_score(rule, habit, commitment, debts, income):
    score = rule["habit_scores"].get(habit, rule["default"])
    if income and debts / income <= rule["low_debt_ratio"]: score += rule["low_debt_bonus"]
    return score - rule["high_commitment_penalty"] if commitment > rule["high_commitment_ratio"] else score
def operation_score(rule, ltv, commitment, purpose):
    score = next((score for maximum, score in rule["ltv_brackets"] if ltv <= maximum), rule["default"])
    if commitment > rule["high_commitment_ratio"]: score -= rule["high_commitment_penalty"]
    return score - rule["second_home_penalty"] if purpose == "second_home" else score
def support_score(rule, role, income, age):
    if role == "none": return rule["no_support_score"]
    if income >= rule["high_income"] and (not age or age <= rule["max_age_for_high_score"]): return rule["high_score"]
    return rule["medium_score"] if income >= rule["medium_income"] else rule["default"]
def amortized_payment(principal, years, annual_rate):
    months = int(years * 12)
    if principal <= 0 or months <= 0: return 0
    rate = annual_rate / 12
    return principal * rate * math.pow(1 + rate, months) / (math.pow(1 + rate, months) - 1)
def classify(rules, score): return next(label for minimum, label in rules if score >= minimum)
def number(value):
    try: return float(value or 0)
    except (TypeError, ValueError): return 0
def clamp(value): return max(0, min(100, round(value)))
def unique(values): return list(dict.fromkeys(values))
