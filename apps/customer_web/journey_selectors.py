"""Evidence-based conversion reports. Heavy reports run in Celery, not HTTP."""
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from statistics import mean
from zoneinfo import ZoneInfo
from django.db.models import Q, Count
from django.utils import timezone
from rest_framework import serializers
from apps.orders.models import Order
from .models import JourneySession, JourneyEvent, AdMetric

NEPAL = ZoneInfo('Asia/Kathmandu')
ATTR_FILTERS = {'source':'utm_source','medium':'utm_medium','campaign':'utm_campaign','ad':'ad_id','adset':'adset_id'}
STAGES = [
    ('Landing page', ['page_view','landing_page_view']), ('Website session',['page_view','session_start']),
    ('Menu / product view',['menu_view','product_view']), ('Product detail',['product_detail_view']),
    ('Add to cart',['add_to_cart']), ('View cart',['cart_view']), ('Checkout started',['checkout_start']),
    ('Delivery information',['delivery_information','address_entered']), ('Delivery area checked',['delivery_area_check']),
    ('Payment method selected',['payment_method_selected']), ('Payment initiated',['payment_started']),
    ('Payment verified',['payment_success']), ('Order submitted',['order_submit']), ('Order created',['order_success']),
    ('Order confirmed',['order_confirmed']),
]
CORE = [('Website',['page_view','landing_page_view']),('Products',['menu_view','product_view','product_detail_view']),
        ('Cart',['add_to_cart']),('Checkout',['checkout_start']),('Order',['order_success'])]
ERRORS = {'api_error','network_error','javascript_error','image_error','order_failed','payment_failed','delivery_area_failed','checkout_validation_failed','coupon_fail'}

class ReportFilters(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    end_date = serializers.DateField(required=False)
    start_time = serializers.TimeField(required=False)
    end_time = serializers.TimeField(required=False)
    source = serializers.CharField(required=False, max_length=120, allow_blank=True)
    medium = serializers.CharField(required=False, max_length=120, allow_blank=True)
    campaign = serializers.CharField(required=False, max_length=120, allow_blank=True)
    ad = serializers.CharField(required=False, max_length=120, allow_blank=True)
    adset = serializers.CharField(required=False, max_length=120, allow_blank=True)
    device = serializers.CharField(required=False, max_length=24, allow_blank=True)
    browser = serializers.CharField(required=False, max_length=24, allow_blank=True)
    os = serializers.CharField(required=False, max_length=24, allow_blank=True)
    location = serializers.CharField(required=False, max_length=120, allow_blank=True)
    product = serializers.CharField(required=False, max_length=120, allow_blank=True)
    category = serializers.CharField(required=False, max_length=120, allow_blank=True)
    returning = serializers.ChoiceField(choices=['','new','returning'], required=False)
    order_status = serializers.CharField(required=False, max_length=24, allow_blank=True)
    payment_method = serializers.CharField(required=False, max_length=32, allow_blank=True)
    search = serializers.CharField(required=False, max_length=120, allow_blank=True)
    live = serializers.BooleanField(default=False)
    abandoned = serializers.BooleanField(default=False)

    def validate(self, data):
        today = timezone.localdate(timezone=NEPAL)
        data.setdefault('start_date', today)
        data.setdefault('end_date', data['start_date'])
        if data['end_date'] < data['start_date'] or (data['end_date']-data['start_date']).days > 90:
            raise serializers.ValidationError('Choose an ordered date range of at most 91 days.')
        if data.get('start_time') and data.get('end_time') and data['end_time'] < data['start_time']:
            raise serializers.ValidationError('End time must follow start time.')
        return data

def bounds(filters):
    start = datetime.combine(filters['start_date'], filters.get('start_time', time.min), NEPAL)
    end = datetime.combine(filters['end_date'], filters.get('end_time', time.max), NEPAL)
    return start, end

def session_query(branch, filters):
    start,end = bounds(filters)
    qs = JourneySession.objects.filter(branch=branch, first_seen__gte=start, first_seen__lte=end)
    for key,field in ATTR_FILTERS.items():
        if filters.get(key):
            qs=qs.filter(**{f'session_touch__{field}':filters[key]})
    for key in ['device','browser','os']:
        if filters.get(key): qs=qs.filter(**{key:filters[key]})
    if filters.get('returning'): qs=qs.filter(returning=filters['returning']=='returning')
    if filters.get('live'): qs=qs.filter(last_seen__gte=timezone.now()-timedelta(seconds=120))
    if filters.get('abandoned'):
        qs=qs.filter(last_seen__lt=timezone.now()-timedelta(minutes=30),events__name__in=['add_to_cart','checkout_start']).exclude(events__name='order_success')
    for key,field in [('location','area'),('product','product_id'),('category','category'),('payment_method','payment_method')]:
        if filters.get(key): qs=qs.filter(**{f'events__metadata__{field}':filters[key]})
    if filters.get('order_status'): qs=qs.filter(events__order__status=filters['order_status'])
    if filters.get('search'):
        value=filters['search']
        match=Q(visitor_hash__icontains=value)|Q(session_hash__icontains=value)|Q(events__order__order_number__icontains=value)|Q(events__metadata__product_name__icontains=value)|Q(session_touch__utm_campaign__icontains=value)
        if value.isdigit(): match |= Q(events__order_id=int(value))
        import uuid
        try: match |= Q(pk=uuid.UUID(value))
        except (ValueError, AttributeError): pass
        qs=qs.filter(match)
    return qs.distinct()

def percent(n, d):
    return round(n/d*100, 2) if d else None

def event_data(event):
    return {'id':str(event.pk), 'event_name':event.name, 'timestamp':event.occurred_at.isoformat(),
            'received_at':event.received_at.isoformat(), 'page':event.path, 'metadata':event.metadata,
            'authority':'server' if event.trusted else 'browser', 'order_id':event.order_id}

def summarize(session, events):
    names={e.name for e in events}
    success=any(e.name=='order_success' and e.trusted for e in events)
    idle=(timezone.now()-session.last_seen).total_seconds()>1800
    cart=next((e.metadata for e in reversed(events) if 'cart_value' in e.metadata), {})
    failures=[e for e in events if e.name in ERRORS]
    meaningful=[e for e in events if e.name not in ('heartbeat','performance','api_timing','exit')]
    status='Ordered' if success else 'Checkout abandoned' if idle and 'checkout_start' in names else 'Cart abandoned' if idle and 'add_to_cart' in names else 'Ended' if idle else 'Active / recent'
    duration=max(0,(session.last_seen-session.first_seen).total_seconds())
    product_views=sum(e.name in ('product_view','product_detail_view') for e in events)
    intent='High' if names & {'add_to_cart','checkout_start'} or product_views>=3 else 'Medium' if product_views>=2 or names & {'scroll','product_search'} or duration>=30 else 'Low'
    area=next((e.metadata.get('area') for e in reversed(events) if e.metadata.get('area')), 'Unknown')
    return {'id':str(session.pk),'anonymous_user_id':session.visitor_hash, 'session_hash':session.session_hash,
        'first_seen':session.first_seen.isoformat(),'last_seen':session.last_seen.isoformat(), 'landing_page':session.landing_path,
        'duration_seconds':round(duration,1),'device':session.device,'browser':session.browser,'os':session.os,'network':session.network,
        'returning':session.returning,'first_touch':session.first_touch,'session_touch':session.session_touch,'last_touch':session.last_touch,
        'source':session.session_touch.get('utm_source') or session.session_touch.get('referrer') or 'direct / unknown',
        'campaign':session.session_touch.get('utm_campaign') or session.session_touch.get('campaign_name') or 'Unknown',
        'ad':session.session_touch.get('ad_id','Unknown'),'adset':session.session_touch.get('adset_id','Unknown'),
        'location':area,'status':status,'converted':success,'intent':intent,'cart_value':float(cart.get('cart_value') or 0),
        'cart':cart.get('items',[]),'last_step':meaningful[-1].name if meaningful else 'No interaction',
        'reason':failures[-1].name if failures and 'abandoned' in status else 'Reason unknown',
        'event_count':len(events),'order_ids':list({e.order_id for e in events if e.order_id}),
        'live':not idle and (timezone.now()-session.last_seen).total_seconds()<120}

def funnel(events, definitions):
    result=[];previous=None
    landing={str(e.session_id) for e in events if e.name in ('page_view','landing_page_view')}
    for index,(label,names) in enumerate(definitions):
        selected=[e for e in events if e.name in names]
        first={}
        for e in selected: first.setdefault(str(e.session_id),e.occurred_at)
        sessions=set(first)
        prior=previous or {}
        progressed={sid for sid in sessions & prior.keys() if first[sid]>=prior[sid]}
        times=[(first[sid]-prior[sid]).total_seconds() for sid in progressed]
        result.append({'stage':label,'events':len(selected),'sessions':len(sessions),
            'users':len({e.session.visitor_hash for e in selected}),'unique_users':len({e.session.visitor_hash for e in selected}),
            'conversion_percent':percent(len(sessions & landing),len(landing)),
            'previous_step_conversion_percent':percent(len(progressed),len(prior)) if index else None,
            'drop_off_percent':round(100-percent(len(progressed),len(prior)),2) if prior else None,
            'dropped_sessions':len(prior)-len(progressed) if index else 0,
            'average_seconds_from_previous':round(mean(times),2) if times else None,
            'average_seconds_to_next':None})
        previous=first
    for index in range(len(result)-1): result[index]['average_seconds_to_next']=result[index+1]['average_seconds_from_previous']
    return result

def group_metrics(summaries, by_session, field):
    grouped=defaultdict(list)
    for row in summaries: grouped[str(row.get(field) or 'Unknown')].append(row)
    rows=[]
    for key,group in grouped.items():
        names=[{e.name for e in by_session[row['id']]} for row in group]
        rows.append({'name':key,'sessions':len(group),'product_viewers':sum(bool(n & {'product_view','product_detail_view','menu_view'}) for n in names),
            'cart':sum('add_to_cart' in n for n in names),'checkout':sum('checkout_start' in n for n in names),
            'orders':sum(r['converted'] for r in group),'conversion_percent':percent(sum(r['converted'] for r in group),len(group)),
            'errors':sum(bool(n & ERRORS) for n in names),'high_intent':sum(r['intent']=='High' for r in group)})
    return sorted(rows,key=lambda r:-r['sessions'])

def build_report(branch, filters):
    # Explicit cap protects workers; incomplete reports are labelled, never presented as complete totals.
    query=session_query(branch,filters)
    events=list(JourneyEvent.objects.filter(session__in=query).select_related('session','order').order_by('occurred_at','received_at','id')[:100001])
    truncated=len(events)>100000
    events=events[:100000]
    by_session=defaultdict(list)
    for event in events: by_session[str(event.session_id)].append(event)
    sessions={str(event.session_id):event.session for event in events}
    summaries=[summarize(session,by_session[sid]) for sid,session in sessions.items()]
    stages=funnel(events,STAGES);core=funnel(events,CORE)
    facts=[]
    errors=defaultdict(list)
    for event in events:
        if event.name in ERRORS:
            key=(event.name,event.metadata.get('endpoint',''),event.metadata.get('status',''),event.metadata.get('error_category',''))
            errors[key].append(event)
    error_rows=[]
    for (name,endpoint,status,category),group in errors.items():
        ids={str(e.session_id) for e in group}
        at_risk=sum(r['cart_value'] for r in summaries if r['id'] in ids and not r['converted'])
        error_rows.append({'event':name,'endpoint':endpoint,'http_status':status,'category':category,'events':len(group),'affected_sessions':len(ids),
            'session_ids':sorted(ids),'cart_value_at_risk':round(at_risk,2),'devices':dict(Counter(e.session.device for e in group))})
    start,end=bounds(filters)
    orders=Order.objects.filter(branch=branch,order_source='WEBSITE',created_at__gte=start,created_at__lte=end)
    database_ids=set(orders.values_list('pk',flat=True))
    # Reconciliation deliberately uses the whole outlet/date, even when segments are selected.
    all_tracked=set(JourneyEvent.objects.filter(order_id__in=database_ids,name='order_success',trusted=True).values_list('order_id',flat=True))
    paid=orders.filter(paid_amount__gt=0,payment_status='PAID').count()
    reconciliation={'scope':'Website orders for this outlet and date range; independent of session segment filters',
        'database_orders':len(database_ids),'analytics_purchases':len(all_tracked),'verified_paid_orders':paid,
        'missing_purchase_tracking':len(database_ids-all_tracked), 'untracked_order_ids':sorted(database_ids-all_tracked)[:100]}
    counts=Counter(e.name for e in events)
    health=[{'event':name,'events':counts[name], 'status':'Observed' if counts[name] else 'Not observed; not proof of broken tracking'}
            for name in ['page_view','product_view','add_to_cart','checkout_start','payment_started','payment_success','order_success']]
    if database_ids-all_tracked:
        facts.append({'kind':'FACT','title':'Orders exist without purchase tracking','evidence':f'{len(database_ids-all_tracked)} of {len(database_ids)} website orders lack a linked purchase event.',
            'severity':'High','confidence':'High','action':'Check browser privacy coverage and checkout attribution. Do not interpret missing events as zero orders.',
            'verification':'Submit a test order and compare its database ID with the journey event.','score':90})
    for error in error_rows:
        facts.append({'kind':'FACT','title':error['event'].replace('_',' ').title(),
            'evidence':f"{error['affected_sessions']} sessions; {error['events']} events; {error['endpoint']} {error['http_status']} {error['category']}",
            'severity':'Critical' if error['event'] in ('payment_failed','order_failed') else 'High','confidence':'High',
            'action':'Reproduce the failing step on the affected device and inspect the corresponding request. An observed error does not prove why every user left.',
            'verification':'Repeat checkout and confirm the failure event disappears and a server order event appears.',
            'score':min(100,60+error['affected_sessions']*5)})
    eligible=[(i,row) for i,row in enumerate(core) if i and core[i-1]['sessions']>=5]
    biggest=max(eligible,key=lambda pair:(pair[1]['dropped_sessions'],pair[1]['drop_off_percent'] or 0),default=None)
    bottleneck=None
    if biggest:
        i,row=biggest
        bottleneck={'from':core[i-1]['stage'],'to':row['stage'],**row}
        facts.append({'kind':'HYPOTHESIS','title':f"Investigate {core[i-1]['stage']} → {row['stage']}",
            'evidence':f"{row['dropped_sessions']} sessions did not make the observed transition ({row['drop_off_percent']}%).",
            'severity':'High','confidence':'Medium' if core[i-1]['sessions']>=30 else 'Low',
            'action':'Review the affected journeys. Test product presentation, delivery information and checkout usability; these are possible causes, not established facts.',
            'verification':'Compare the same traffic segment after a controlled change.', 'score':min(80,row['dropped_sessions'])})
    if not summaries: facts.append({'kind':'FACT','title':'No instrumented sessions in this selection','evidence':'Legacy page views cannot reconstruct cart or checkout activity.',
        'severity':'Low','confidence':'High','action':'Open the customer site and verify events in Event Debugger.','verification':'See a page_view and a cart event in one session.','score':0})
    elif not facts:
        facts.append({'kind':'FACT','title':'Insufficient evidence to identify a cause',
            'evidence':f'{len(summaries)} sessions observed; no recorded errors or sufficiently populated funnel transition.',
            'severity':'Low','confidence':'High','action':'Collect more sessions and compare the same traffic segment. A small sample cannot establish why visitors leave.',
            'verification':'Check Event Debugger and complete a test checkout.','score':0})
    groups={field:group_metrics(summaries,by_session,field) for field in ['source','campaign','ad','adset','device','browser','os','network','location','returning','intent']}
    paths=Counter(' → '.join([sessions[sid].session_touch.get('utm_source','direct')]+[e.path if e.name=='page_view' else e.name for e in rows if e.name in {'page_view','product_view','add_to_cart','checkout_start','order_success','exit'}][:10]) for sid,rows in by_session.items())
    products=defaultdict(lambda:{'view':set(),'cart':set(),'checkout':set(),'purchase':set(),'revenue':0,'name':''})
    for event in events:
        meta=event.metadata
        items=meta.get('items',[]) if event.name in ('checkout_start','order_success') else [meta]
        for item in items:
            key=str(item.get('product_id',''))
            if not key: continue
            row=products[key];row['name']=item.get('product_name',key)
            stage='view' if event.name in ('product_view','product_detail_view') else 'cart' if event.name=='add_to_cart' else 'checkout' if event.name=='checkout_start' else 'purchase' if event.name=='order_success' and event.trusted else None
            if stage: row[stage].add(str(event.session_id))
            if stage=='purchase': row['revenue']+=float(item.get('unit_price',0))*float(item.get('quantity',0))
    product_rows=[{'id':key,'name':row['name'],**{stage:len(row[stage]) for stage in ['view','cart','checkout','purchase']},
        'cart_rate':percent(len(row['cart'] & row['view']),len(row['view'])),'purchase_rate':percent(len(row['purchase'] & row['view']),len(row['view'])),
        'revenue_before_discounts':round(row['revenue'],2)} for key,row in products.items()]
    performance=defaultdict(list)
    latest_vitals={}
    for event in events:
        if event.name in ('performance','api_timing'):
            metric=event.metadata.get('metric') or event.metadata.get('endpoint','API')
            key=(metric,event.session.device,event.session.browser,event.session.os,event.session.network)
            value=float(event.metadata.get('value',event.metadata.get('duration_ms',0)))
            if event.name=='performance': latest_vitals[(str(event.session_id),event.path,metric)]=(key,value)
            else: performance[key].append(value)
    for key,value in latest_vitals.values(): performance[key].append(value)
    timing_rows=[{'metric':k[0],'device':k[1],'browser':k[2],'os':k[3],'network':k[4],'samples':len(v),'average':round(mean(v),2),'p95':sorted(v)[min(len(v)-1,int(len(v)*.95))]} for k,v in performance.items()]
    price_bands=[]
    for low,high in [(0,200),(200,400),(400,600),(600,float('inf'))]:
        rows=[r for r in summaries if low<=r['cart_value']<high and 'add_to_cart' in {e.name for e in by_session[r['id']]}]
        ended=[r for r in rows if r['converted'] or 'abandoned' in r['status']]
        abandoned=sum('abandoned' in r['status'] for r in ended)
        price_bands.append({'band':f'{low}–{high}' if high!=float('inf') else '600+','cart_sessions':len(rows),'ended_sessions':len(ended),'abandoned':abandoned,'abandonment_percent':percent(abandoned,len(ended))})
    hourly=[]
    for hour in range(24):
        rows=[r for r in summaries if sessions[r['id']].first_seen.astimezone(NEPAL).hour==hour]
        hourly.append({'hour':hour,'sessions':len(rows),'cart':sum('add_to_cart' in {e.name for e in by_session[r['id']]} for r in rows),'orders':sum(r['converted'] for r in rows)})
    fee_sessions={str(e.session_id) for e in events if e.name=='delivery_fee_view'}
    fees={'exposed_sessions':len(fee_sessions),'abandoned_after_exposure':sum(r['id'] in fee_sessions and 'abandoned' in r['status'] for r in summaries),
          'interpretation':'Association only. A fee view followed by abandonment does not establish price as the cause.'}
    ad_rows=AdMetric.objects.filter(branch=branch,date__gte=filters['start_date'],date__lte=filters['end_date'])
    for key,field in [('source','source'),('ad','ad_id'),('adset','adset_id')]:
        if filters.get(key): ad_rows=ad_rows.filter(**{field:filters[key]})
    ads=list(ad_rows.values('date','source','campaign_id','adset_id','ad_id','impressions','clicks','spend'))
    ad_compatible=not any(filters.get(k) for k in ['campaign','medium','device','browser','os','network','location','product','category','returning','order_status','payment_method','search','start_time','end_time'])
    ad_totals={key:sum(float(r[key]) for r in ads) if ads and ad_compatible else None for key in ['clicks','impressions','spend']}
    tracked_orders={e.order_id:e.order for e in events if e.trusted and e.name=='order_success' and e.order_id}
    revenue=sum(float(o.total_payable) for o in tracked_orders.values() if o.status!='CANCELLED')
    from .journey_diagnostics import baseline_report
    baseline=baseline_report(branch,filters,{'sessions':len(summaries),'converted_sessions':sum(r['converted'] for r in summaries)})
    if baseline['significant_decline']:
        facts.append({'kind':'SIGNAL','title':'Conversion declined against the previous seven days',
            'evidence':f"Previous: {baseline['conversion_percent']}%; selected period: {baseline['current_conversion_percent']}%. z={baseline['z_score']}.",
            'severity':'High','confidence':'Medium','action':'Compare checkout errors, traffic mix and device performance before attributing the decline to a cause.',
            'verification':'Repeat the comparison after fixing a measured issue with a comparable traffic sample.','score':85})
    devices={row['name']:row for row in groups['device']}
    mobile,desktop=devices.get('MOBILE'),devices.get('DESKTOP')
    if mobile and desktop and mobile['sessions']>=30 and desktop['sessions']>=30 and mobile['errors']/mobile['sessions']>desktop['errors']/desktop['sessions']*2 and mobile['errors']>=5:
        facts.append({'kind':'SIGNAL','title':'Mobile sessions show a higher error rate',
            'evidence':f"Mobile: {mobile['errors']}/{mobile['sessions']} sessions with errors. Desktop: {desktop['errors']}/{desktop['sessions']}.",
            'severity':'High','confidence':'Medium','action':'Reproduce the affected request on the listed mobile browsers.','verification':'Confirm fewer failing mobile sessions and compare conversion again.','score':80})
    attempts=Counter(e.metadata.get('attempt_id') for e in events if e.name=='order_submit' and e.metadata.get('attempt_id'))
    duplicates=[{'event':'order_submit','identity':key,'events':value,'explanation':'Repeated attempts, not additional orders. Server purchase events are unique per order.'} for key,value in attempts.items() if value>1]
    payment_rows=[event_data(e) for e in events if e.name.startswith('payment_')]
    return {'generated_at':timezone.now().isoformat(),'timezone':'Asia/Kathmandu','filters':{k:str(v) for k,v in filters.items()},'truncated':truncated,
        'scope_note':'Sessions starting in the selected Nepal-time range. Browser coverage excludes privacy opt-outs. Recent sessions are not counted as abandoned until 30 minutes without activity.',
        'executive':{'sessions':len(summaries),'visitors':len({s.visitor_hash for s in sessions.values()}),'engaged_sessions':sum(r['intent']!='Low' for r in summaries),
            'cart':sum('add_to_cart' in {e.name for e in by_session[r['id']]} for r in summaries),'checkout':sum('checkout_start' in {e.name for e in by_session[r['id']]} for r in summaries),
            'orders':len(tracked_orders),'revenue':round(revenue,2),'conversion_percent':percent(sum(r['converted'] for r in summaries),len(summaries)),**ad_totals,
            'cost_per_order':round(ad_totals['spend']/len(tracked_orders),2) if ad_totals['spend'] is not None and tracked_orders else None},
        'funnel':stages,'core_funnel':core,'bottleneck':bottleneck,'diagnosis':sorted(facts,key=lambda r:-r['score']), 'baseline':baseline,'duplicates':duplicates,
        'sessions':sorted(summaries,key=lambda r:r['last_seen'],reverse=True)[:100], 'session_count':len(summaries),
        'groups':groups,'products':sorted(product_rows,key=lambda r:-r['view']),'paths':[{'path':k,'sessions':v,'percent':percent(v,len(summaries))} for k,v in paths.most_common(20)],
        'abandoned':[r for r in summaries if 'abandoned' in r['status']][:100], 'errors':error_rows, 'performance':timing_rows,'cart_bands':price_bands,
        'delivery_fees':fees,'hourly':hourly,'payments':payment_rows[-100:], 'ads':ads,
        'checkout_fields':[{'step':n,'sessions':len({str(e.session_id) for e in events if e.name==n})} for n in ['checkout_start','name_entered','phone_entered','address_entered','location_selected','payment_method_selected','order_submit','order_success']],
        'reconciliation':reconciliation,'tracking_health':health,
        'limitations':['Ad metrics require imported Ads reporting; landing attribution is not an ad click count.',
            'QR proof upload is not verified payment. Area serviceability and delivery fees are not implemented by the current checkout.',
            'No session replay provider is installed. Journeys replay recorded events, not the screen.',
            'Web vitals are available only from browsers emitting supported metrics.','Historical events cannot be reconstructed.']}
