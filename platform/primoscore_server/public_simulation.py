"""QR entry estimates and consultant leads; voucher identity stays unchanged."""
import math
from flask import request, jsonify
from .auth import AuthError
from .customer import Customer
from .repository import encode, new_id
from .database import now
from primoscore_core.intake import clean_answers, complete_result
from primoscore_core.score_engine import calculate_subsistence, load_constitution


def evaluate(mode, incoming):
    try:
        answers = clean_answers(incoming)
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
        r = .04/12
        capital = math.floor((rata*(1-(1+r)**(-years*12))/r)/1000)*1000
        return answers, dict(engineVersion='primoscore-maximum-1', simulationMode='max', classification='stima_teorica', maxLoan=capital, effectiveTerm=years, threshold=threshold, referenceYear=subsistence['referenceYear'], metrics=dict(totalHouseholdIncome=income), strengths=[], warnings=['Stima teorica e puramente indicativa, da approfondire con il consulente.'])
    except (ValueError, KeyError) as error:
        raise AuthError(str(error)) from error


def register_public_simulation(app, auth, local):
    service = Customer(auth, local=local)

    @app.post('/api/public-simulation/estimate')
    def estimate():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)!={'code','mode','answers'}:
            raise AuthError('Richiesta non valida.')
        service.context(data['code'])
        auth.rate('public-estimate',request.remote_addr,30,900)
        _,result=evaluate(data['mode'],data['answers'])
        return jsonify(result)

    @app.post('/api/public-simulation/lead')
    def lead():
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)!={'mode','answers','contact','residence_city'} or not isinstance(data['contact'],dict):
            raise AuthError('Richiesta non valida.')
        context=service.context(data['contact'].get('code'))
        city=data['residence_city']
        if not isinstance(city,str) or not city.strip() or len(city)>100:
            raise AuthError('Inserisci la città di residenza.')
        # Calculate before creating the contact. No client-supplied result is trusted.
        try:
            answers,result=evaluate(data['mode'],data['answers'])
        except AuthError as error:
            if data['mode']!='max' or not str(error).startswith('La soglia ISTAT non è disponibile'):
                raise
            answers=clean_answers(data['answers']);result=None
        prefix=app.config['PRIMOSCORE_COOKIE_PREFIX']
        acquired=service.acquire(data['contact'],request.cookies.get(prefix+'guest',''),request.remote_addr)
        if acquired.get('resume_required'):
            return jsonify(acquired)
        guest=acquired.pop('guest_token',None) or request.cookies.get(prefix+'guest','')
        current=service.get(guest=guest)
        revision=service.save(dict(answers=answers,step=5,revision=current['questionnaire']['revision'],residence_city=city.strip()),guest=guest)['revision']
        with auth.db.transaction() as c:
            t=current['client']['tenant_id'];ident=current['client']['id'];q=current['questionnaire']['id']
            if result is not None:
                c.execute('INSERT INTO assessments(tenant_id,id,client_id,questionnaire_id,answers_revision,engine_version,answers_json,result_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(t,new_id(),ident,q,revision,result['engineVersion'],encode(answers),encode(result),now()))
            service.event(c,t,ident,'consultation_requested_'+data['mode'])
        response=jsonify(ok=True,testing=context['testing'])
        response.set_cookie(prefix+'guest',guest,max_age=86400,httponly=True,secure=not local,samesite='Strict',path='/')
        return response
