"""CiceroEV V2 completion semantics, adapted to consultant-neutral presentation.

Reference: voucher_v2.py::complete at fa2fc5763cc54249e890e26d770f2e3be8cfdd88.
The underlying score engine and thresholds are unchanged.
"""
from .questionnaire import QUESTION_RULES, validate_partial_step, validate_complete_answers
from .score_engine import calculate_score
INTAKE_VERSION='primoscore-v2-intake-1'
EXTRAS={'propertyFound':{'yes','no'},'timeframe':{'soon','months','later','exploring'},'iseeUnknown':{'yes'}}


def clean_answers(incoming):
    if not isinstance(incoming,dict):raise ValueError('Risposte non valide.')
    rules={k:v for group in QUESTION_RULES.values() for k,v in group.items()}
    if set(incoming)-set(rules)-set(EXTRAS):raise ValueError('Il questionario contiene campi non consentiti.')
    for key,value in incoming.items():
        if value is None or value=='':continue
        if key in EXTRAS:
            if not isinstance(value,str) or value not in EXTRAS[key]:raise ValueError('Risposta non valida: '+key)
        elif rules[key]['type']=='integer':
            # Harden the HTTP boundary; int(True) and int(1.9) must not become answers.
            if type(value) is not int and not (isinstance(value,str) and value.isascii() and value.isdigit()):raise ValueError('Inserisci un numero intero: '+key)
        elif not isinstance(value,str):raise ValueError('Risposta non valida: '+key)
    cleaned={}
    for step,group in QUESTION_RULES.items():
        part,errors=validate_partial_step(step,{k:v for k,v in incoming.items() if k in group})
        if errors:raise ValueError(next(iter(errors.values())))
        cleaned.update(part)
    cleaned.update({k:v for k,v in incoming.items() if k in EXTRAS and v not in (None,'')})
    if cleaned.get('supportRole')=='none':
        cleaned.pop('supportAge',None);cleaned.pop('supportIncome',None)
    if cleaned.get('iseeUnknown')=='yes':cleaned.pop('iseeBand',None)
    return cleaned


def complete_result(answers):
    answers=clean_answers(answers)
    missing=validate_complete_answers(answers)
    if answers.get('iseeUnknown')=='yes':missing=[k for k in missing if k!='iseeBand']
    if answers.get('propertyFound')=='no':missing=[k for k in missing if k not in ('propertyPrice','loanAmount')]
    if missing:raise ValueError('Completa le risposte richieste: '+', '.join(missing))
    if answers['children']>=answers['householdSize'] or answers['householdEarners']>answers['householdSize']:
        raise ValueError('Controlla componenti del nucleo, figli e persone con un reddito.')
    if answers.get('propertyFound')=='no' and not all(answers.get(k) for k in ('propertyPrice','loanAmount')):
        result={'partial':True,'engineVersion':'cicero-v2-intake-1','classification':'da_approfondire','strengths':['Hai descritto il tuo progetto e la tua situazione.'],'warnings':['Per stimare la rata servono importo del mutuo e valore dell’immobile.'],'metrics':{},'consap':{'explanation':'Verifica da completare con il tuo consulente.'}}
    else:
        result=calculate_score(answers)
        if not answers.get('istatRegion'):
            result['warnings']=[('La verifica della sostenibilità con la soglia ISTAT richiede ulteriori dati sul nucleo, da approfondire con il tuo consulente.' if 'ISTAT' in w else w) for w in result['warnings']]
        if not (answers.get('applicantAge',0)<36 or (answers.get('supportRole')=='coapplicant' and answers.get('supportAge',90)<36)) or answers.get('iseeUnknown')=='yes':
            result['consap']={'accessStatus':'manual_review','explanation':'L’accesso al Fondo dipende anche da categorie e requisiti ulteriori. Il tuo consulente verificherà la tua situazione; questa valutazione non ti esclude dal Fondo.'}
    result['intakeVersion']=INTAKE_VERSION
    return result
