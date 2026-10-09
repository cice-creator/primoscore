"""QR entry estimates and consultant leads; voucher identity stays unchanged."""
import math
from .auth import AuthError
from primoscore_core.intake import clean_answers, complete_result
from primoscore_core.score_engine import calculate_subsistence, load_constitution


def evaluate(mode, incoming):
    try:
        answers = clean_answers(incoming)
        if answers.get('supportRole')=='coapplicant' and answers.get('supportInHousehold') not in ('yes','no'):
            raise ValueError('Indica se il cointestatario fa parte del nucleo familiare.')
        if mode == 'score':
            answers['propertyFound'] = 'yes'
            return answers, complete_result(answers)
        if mode != 'max':
            raise ValueError('Percorso non valido.')
        for key in ('propertyPrice', 'loanAmount'):
            answers.pop(key, None)
        answers['propertyFound'] = 'no'
        # Reuse full questionnaire validation without calculating an invented operation.
        complete_result(answers)
        oldest = max(answers['applicantAge'], answers.get('supportAge', 0) if answers['supportRole']=='coapplicant' else 0)
        years = min(answers['loanTerm'], 80-oldest)
        if years <= 0:
            raise ValueError('Non è disponibile una durata positiva entro gli 80 anni.')
        income = answers['monthlyIncome'] + (answers.get('supportIncome', 0) if answers['supportRole']=='coapplicant' else 0)
        debts = answers['monthlyDebts']
        subsistence = calculate_subsistence(load_constitution()['istat_sussistenza'], answers, income, debts, 0)
        if subsistence['status']=='unavailable':
            raise ValueError('La soglia ISTAT non è disponibile. Riprova oppure richiedi una consulenza per verificare le tue possibilità.')
        threshold = subsistence['threshold']
        rata = max(0, min(income*.5-debts, income-debts-threshold))
        r = load_constitution()['calculation']['assumptions']['annual_interest_rate']/12
        capital = math.floor((rata*(1-(1+r)**(-years*12))/r)/1000)*1000
        return answers, dict(engineVersion='primoscore-maximum-1', simulationMode='max', classification='stima_teorica', maxLoan=capital, effectiveTerm=years, maximumPayment=round(rata,2), threshold=threshold, referenceYear=subsistence['referenceYear'], metrics=dict(totalHouseholdIncome=income), strengths=[], warnings=['Stima teorica e puramente indicativa, da approfondire con il consulente.'])
    except (ValueError, KeyError) as error:
        raise AuthError(str(error)) from error


def register_public_simulation(app, auth, local):
    # The former anonymous endpoints must never disclose a result before email verification.
    @app.post('/api/public-simulation/estimate')
    @app.post('/api/public-simulation/lead')
    def retired_public_estimate():
        raise AuthError('Il percorso è stato aggiornato. Ricarica la pagina e registra il contatto prima di continuare.',410)
