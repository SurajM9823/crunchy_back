from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from .audience import WebsiteAnalyticsView, audience_branch, VisitThrottle
from .journey_schema import BatchInput
from .journey_services import ingest
from .journey_selectors import ReportFilters, session_query, summarize, event_data
from .models import JourneySession, JourneyEvent, JourneyReport, AdMetric
from django.shortcuts import get_object_or_404

class EventBatchView(WebsiteAnalyticsView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VisitThrottle]

    def post(self, request):
        from django.conf import settings
        if settings.WEBSITE_ANALYTICS_PROVIDER == 'posthog':
            return Response({'detail':'Visitor analytics has moved to PostHog.'}, status=410)
        serializer = BatchInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(ingest(serializer.validated_data))

def filters_for(request):
    serializer=ReportFilters(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data

class IntelligenceView(WebsiteAnalyticsView):
    def get(self, request):
        from .journey_services import request_report
        branch=audience_branch(request)
        report=request_report(branch,filters_for(request))
        return Response({'id':str(report.pk),'status':report.status,'data':report.result or None,
            'message':'Report queued. The analytics worker will publish it automatically.' if report.status=='PENDING' else ''})

class JourneyListView(WebsiteAnalyticsView):
    def get(self, request):
        branch=audience_branch(request)
        query=session_query(branch,filters_for(request))
        page=serializers.IntegerField(min_value=1,max_value=100000).run_validation(request.query_params.get('page',1))
        rows=query.order_by('-last_seen','id')[(page-1)*25:page*25]
        results=[summarize(row,list(row.events.order_by('occurred_at','received_at','id'))) for row in rows]
        return Response({'count':query.count(),'results':results,'page':page,'page_size':25})

class JourneyDetailView(WebsiteAnalyticsView):
    def get(self, request, session_id):
        branch=audience_branch(request)
        row=get_object_or_404(JourneySession,branch=branch,pk=session_id)
        page=serializers.IntegerField(min_value=1,max_value=100000).run_validation(request.query_params.get('page',1))
        events=row.events.select_related('order').order_by('occurred_at','received_at','id')
        visitor_sessions=JourneySession.objects.filter(branch=branch,visitor_hash=row.visitor_hash)
        # Include the full event history so older purchases cannot become abandoned carts.
        latest=list(row.events.order_by('occurred_at','received_at','id'))
        return Response({'session':summarize(row,latest),'count':events.count(),'page':page,'page_size':100,
            'visitor_session_count':visitor_sessions.count(),'visitor_first_seen':visitor_sessions.order_by('first_seen').first().first_seen,
            'events':[event_data(e) for e in events[(page-1)*100:page*100]], 'replay':None})

class EventDebuggerView(WebsiteAnalyticsView):
    def get(self,request):
        branch=audience_branch(request)
        sessions=session_query(branch,filters_for(request))
        page=serializers.IntegerField(min_value=1,max_value=100000).run_validation(request.query_params.get('page',1))
        events=JourneyEvent.objects.filter(session__in=sessions).select_related('session').order_by('-received_at','-id')
        return Response({'count':events.count(),'page':page,'results':[{**event_data(e),'session_id':str(e.session_id)} for e in events[(page-1)*100:page*100]]})

class AdMetricInput(serializers.Serializer):
    date=serializers.DateField()
    source=serializers.ChoiceField(choices=['facebook','instagram','google','other'],default='facebook')
    campaign_id=serializers.CharField(max_length=120,allow_blank=True,default='')
    adset_id=serializers.CharField(max_length=120,allow_blank=True,default='')
    ad_id=serializers.CharField(max_length=120)
    clicks=serializers.IntegerField(min_value=0,max_value=100000000)
    impressions=serializers.IntegerField(min_value=0,max_value=100000000)
    spend=serializers.DecimalField(max_digits=12,decimal_places=2,min_value=0)

class AdImportView(WebsiteAnalyticsView):
    def post(self,request):
        from django.db import transaction
        from rest_framework.exceptions import PermissionDenied
        branch=audience_branch(request)
        if request.user.role not in ('RESTAURANT_OWNER','BRANCH_MANAGER') and not request.user.is_superuser:
            raise PermissionDenied('Ad imports require an owner or manager.')
        if not isinstance(request.data,list) or len(request.data)>500:
            raise serializers.ValidationError('Provide at most 500 daily ad rows.')
        serializer=AdMetricInput(data=request.data,many=True);serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            for row in serializer.validated_data:
                keys={key:row[key] for key in ['date','source','ad_id']}
                AdMetric.objects.update_or_create(branch=branch,**keys,defaults=row)
        return Response({'imported':len(serializer.validated_data)})

class JourneyExportView(WebsiteAnalyticsView):
    def get(self,request):
        import csv, io, json
        from django.http import StreamingHttpResponse
        from django.core.serializers.json import DjangoJSONEncoder
        branch=audience_branch(request);filters=filters_for(request)
        kind=serializers.ChoiceField(choices=['events','sessions','funnel','campaigns','abandoned','errors']).run_validation(request.query_params.get('kind','events'))
        fmt=serializers.ChoiceField(choices=['csv','json']).run_validation(request.query_params.get('export_format','json'))
        sessions=session_query(branch,filters)
        if kind=='events':
            rows=({**event_data(e),'session_id':str(e.session_id)} for e in JourneyEvent.objects.filter(session__in=sessions).order_by('occurred_at','id').iterator(chunk_size=500))
        elif kind in ('sessions','abandoned'):
            rows=(summarize(s,list(s.events.order_by('occurred_at','received_at','id'))) for s in sessions.order_by('first_seen','id').iterator(chunk_size=100))
            if kind=='abandoned': rows=(r for r in rows if 'abandoned' in r['status'])
        else:
            from .journey_services import request_report
            report=request_report(branch,filters)
            if report.status!='READY': return Response({'detail':'Wait for the report to finish before exporting aggregates.'},status=409)
            rows=report.result['groups']['campaign'] if kind=='campaigns' else report.result[kind]
        def stream():
            if fmt=='json':
                yield '['
                for i,row in enumerate(rows): yield (',' if i else '')+json.dumps(row,cls=DjangoJSONEncoder)
                yield ']'
            else:
                buffer=io.StringIO();writer=csv.writer(buffer);columns=None
                for row in rows:
                    if columns is None: columns=list(row);writer.writerow(columns)
                    values=[]
                    for key in columns:
                        value=row.get(key,'')
                        text=json.dumps(value,cls=DjangoJSONEncoder) if isinstance(value,(dict,list)) else str(value)
                        values.append("'"+text if text.lstrip().startswith(('=','+','-','@')) else text)
                    writer.writerow(values);yield buffer.getvalue();buffer.seek(0);buffer.truncate(0)
        response=StreamingHttpResponse(stream(),content_type='application/json' if fmt=='json' else 'text/csv')
        response['Content-Disposition']=f'attachment; filename="analytics-{kind}.{fmt}"'
        response['Cache-Control']='private, no-store'
        return response

class AnalystView(WebsiteAnalyticsView):
    def post(self,request):
        import hashlib
        from django.conf import settings
        from .journey_schema import safe_text
        from .journey_diagnostics import analyst_answer
        branch=audience_branch(request)
        report_id=serializers.UUIDField().run_validation(request.data.get('report_id'))
        report=get_object_or_404(JourneyReport,branch=branch,pk=report_id,status='READY')
        if 'executive' not in report.result:
            raise serializers.ValidationError('Select a conversion report to analyze.')
        question=safe_text(serializers.CharField(max_length=300).run_validation(request.data.get('question')),300)
        ai=bool(settings.ANALYTICS_OPENAI_API_KEY and settings.ANALYTICS_OPENAI_MODEL)
        if not ai:
            return Response({'status':'READY','data':analyst_answer(report.result,question),'ai_configured':False})
        key=hashlib.sha256(f'analyst:{report.pk}:{question}'.encode()).hexdigest()
        job,_=JourneyReport.objects.get_or_create(key=key,defaults={'branch':branch,'filters':{'_report_id':str(report.pk),'_question':question}})
        return Response({'id':str(job.pk),'status':job.status,'data':job.result or None,'ai_configured':True})

    def get(self,request,report_id):
        branch=audience_branch(request)
        report=get_object_or_404(JourneyReport,branch=branch,pk=report_id)
        return Response({'id':str(report.pk),'status':report.status,'data':report.result or None})
