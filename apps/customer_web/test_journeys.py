import uuid
import json
from io import BytesIO
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.cache import cache
from rest_framework.test import APIClient
from apps.restaurants.models import Restaurant, Branch
from apps.user_accounts.models import User
from apps.orders.models import Order
from .models import JourneyEvent, JourneySession, WebsiteVisit

@override_settings(WEBSITE_ANALYTICS_PROVIDER='legacy')
class JourneyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username='journey-owner', role='RESTAURANT_OWNER')
        org = Restaurant.objects.create(name='Journeys', admin=self.owner)
        self.owner.restaurant = org
        self.owner.save()
        self.branch = Branch.objects.create(restaurant=org, name='One', branch_code='J1')
        other = Restaurant.objects.create(name='Other', admin=self.owner)
        self.other = Branch.objects.create(restaurant=other, name='Other', branch_code='J2')
        self.client = APIClient()
        self.identity = {'visitor_id':str(uuid.uuid4()), 'session_id':str(uuid.uuid4())}

    def batch(self, names, **extra):
        return {**self.identity, 'outlet_id':self.branch.pk, 'device':'MOBILE', 'browser':'Chrome', 'os':'Android',
                'attribution':{'utm_source':'facebook', 'ad_id':'burger-video'},
                'events':[{'event_id':str(uuid.uuid4()), 'name':name, 'timestamp':timezone.now().isoformat(),
                           'path':'/menu?token=private', 'metadata':{}} for name in names], **extra}

    def send(self, body):
        response = self.client.post('/api/v1/customer/events/', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_ingestion_is_private_deduplicated_and_preserves_existing_visits(self):
        data = self.batch(['page_view','add_to_cart'])
        data['events'][1]['metadata'] = {'cart_value':200, 'password':'secret', 'phone':'9841234567', 'address':'private', 'product_name':'Burger'}
        self.assertEqual(self.send(data)['accepted'], 2)
        self.assertEqual(self.send(data)['duplicates'], 2)
        self.assertEqual(JourneyEvent.objects.count(), 2)
        self.assertEqual(WebsiteVisit.objects.count(), 1)
        event = JourneyEvent.objects.get(name='add_to_cart')
        self.assertEqual(event.path, '/menu')
        self.assertEqual(event.metadata, {'cart_value':200.0, 'product_name':'Burger'})
        self.assertNotEqual(event.session.visitor_hash, self.identity['visitor_id'])

    def test_browser_cannot_forge_purchase_and_bad_batches_are_atomic(self):
        data = self.batch(['page_view','order_success'])
        self.assertEqual(self.client.post('/api/v1/customer/events/', data, format='json').status_code, 400)
        self.assertEqual(JourneyEvent.objects.count(), 0)

    def test_returning_and_touch_attribution(self):
        self.send(self.batch(['page_view']))
        self.identity['session_id'] = str(uuid.uuid4())
        self.send(self.batch(['page_view'], attribution={'utm_source':'google'}))
        session = JourneySession.objects.order_by('-first_seen').first()
        self.assertTrue(session.returning)
        self.assertEqual(session.first_touch['utm_source'], 'facebook')
        self.assertEqual(session.session_touch['utm_source'], 'google')

    def test_unknown_metadata_and_precise_locations_are_not_retained(self):
        data = self.batch(['location_selected'])
        data['events'][0]['metadata'] = {'latitude':27.5, 'longitude':85.3, 'area':'someone@example.com'}
        self.send(data)
        self.assertEqual(JourneyEvent.objects.get().metadata, {'area':'[redacted]'})

    def test_four_acceptance_journeys_reconcile_orders_and_failure(self):
        from .journey_services import attach_order, sync_order_outcomes
        from .journey_selectors import build_report, ReportFilters
        sequences = [
            ['page_view','product_view','add_to_cart','checkout_start','payment_started','order_submit'],
            ['page_view','product_view','add_to_cart','checkout_start','payment_started','payment_failed','exit'],
            ['page_view','product_view','exit'],
            ['page_view','add_to_cart','delivery_area_check','delivery_area_failed'],
        ]
        for index, names in enumerate(sequences):
            self.identity = {'visitor_id':str(uuid.uuid4()), 'session_id':str(uuid.uuid4())}
            body = self.batch(names)
            for number,event in enumerate(body['events']):
                event['timestamp']=(timezone.now()-timedelta(minutes=40)+timedelta(seconds=number)).isoformat()
                event['metadata']={'cart_value':200, 'product_id':'burger'}
            self.send(body)
            if index==0:
                order=Order.objects.create(branch=self.branch, order_number='JOURNEY-1', order_source='WEBSITE', status='ACCEPTED',total_payable=200,paid_amount=200,payment_status='PAID')
                attach_order(order,self.identity)
                attach_order(order,self.identity)
                sync_order_outcomes(order)
        filters=ReportFilters(data={});self.assertTrue(filters.is_valid())
        report=build_report(self.branch,filters.validated_data)
        self.assertEqual(report['executive']['orders'],1)
        self.assertEqual(report['reconciliation']['database_orders'],1)
        self.assertEqual(report['reconciliation']['analytics_purchases'],1)
        self.assertEqual(report['reconciliation']['verified_paid_orders'],1)
        self.assertEqual(report['executive']['sessions'],4)
        self.assertEqual(sum(row['status']=='Checkout abandoned' for row in report['abandoned']),1)
        self.assertEqual(next(row for row in report['errors'] if row['event']=='payment_failed')['affected_sessions'],1)
        self.assertEqual(next(row for row in report['errors'] if row['event']=='delivery_area_failed')['affected_sessions'],1)
        self.assertEqual(report['executive']['clicks'],None)
        self.assertEqual(JourneyEvent.objects.filter(name='order_success').count(),1)

    def test_missing_purchase_is_distinguished_from_no_orders(self):
        from .journey_selectors import build_report, ReportFilters
        Order.objects.create(branch=self.branch,order_number='UNTRACKED',order_source='WEBSITE')
        filters=ReportFilters(data={});filters.is_valid(raise_exception=True)
        result=build_report(self.branch,filters.validated_data)
        self.assertEqual(result['reconciliation']['missing_purchase_tracking'],1)
        self.assertTrue(any('Orders exist' in row['title'] for row in result['diagnosis']))

    def test_journey_endpoints_are_scoped_and_export_does_not_leak(self):
        self.send(self.batch(['page_view','add_to_cart']))
        session=JourneySession.objects.get()
        url=f'/api/v1/customer/journeys/{session.pk}/?outlet_id={self.branch.pk}'
        self.assertIn(self.client.get(url).status_code,[401,403])
        self.client.force_authenticate(self.owner)
        result=self.client.get(url)
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual([r['event_name'] for r in result.data['events']],['page_view','add_to_cart'])
        self.assertEqual(result['Cache-Control'],'private, no-store')
        self.assertEqual(self.client.get(f'/api/v1/customer/journeys/{session.pk}/?outlet_id={self.other.pk}').status_code,403)
        exported=self.client.get(f'/api/v1/customer/intelligence-export/?outlet_id={self.branch.pk}&kind=events&format=json')
        self.assertEqual(exported.status_code,200)
        import json
        rows=json.loads(b''.join(exported.streaming_content))
        self.assertEqual(len(rows),2)

    def test_report_runs_in_worker_and_reuses_identical_snapshot(self):
        from .tasks import build_journey_reports
        self.send(self.batch(['page_view']))
        self.client.force_authenticate(self.owner)
        path=f'/api/v1/customer/intelligence/?outlet_id={self.branch.pk}'
        one=self.client.get(path)
        self.assertEqual(one.status_code,200,one.data)
        self.assertEqual(one.data['status'],'PENDING')
        build_journey_reports()
        two=self.client.get(path)
        self.assertEqual(two.data['id'],one.data['id'])
        self.assertEqual(two.data['status'],'READY')
        self.assertEqual(two.data['data']['executive']['sessions'],1)

    def test_live_and_abandoned_filters_and_csv_formula_safety(self):
        self.send(self.batch(['page_view','checkout_start']))
        old=JourneySession.objects.get()
        JourneySession.objects.filter(pk=old.pk).update(last_seen=timezone.now()-timedelta(minutes=40))
        self.identity={'visitor_id':str(uuid.uuid4()),'session_id':str(uuid.uuid4())}
        self.send(self.batch(['page_view']))
        self.client.force_authenticate(self.owner)
        root=f'/api/v1/customer/journeys/?outlet_id={self.branch.pk}'
        live=self.client.get(root+'&live=true').data
        abandoned=self.client.get(root+'&abandoned=true').data
        self.assertEqual(live['count'],1)
        self.assertEqual(abandoned['count'],1)
        self.assertEqual(abandoned['results'][0]['id'],str(old.pk))
        JourneySession.objects.filter(pk=old.pk).update(landing_path='=SUM(1,2)')
        response=self.client.get(f'/api/v1/customer/intelligence-export/?outlet_id={self.branch.pk}&kind=sessions&export_format=csv')
        self.assertEqual(response.status_code,200)
        self.assertIn(b"'=SUM",b''.join(response.streaming_content))

    def test_ad_import_is_idempotent_and_numeric_meta_ids_survive(self):
        data=self.batch(['page_view'],attribution={'ad_id':'123456789012345','campaign_id':'987654321098765'})
        self.send(data)
        self.assertEqual(JourneySession.objects.get().session_touch['ad_id'],'123456789012345')
        self.client.force_authenticate(self.owner)
        body=[{'date':timezone.localdate().isoformat(),'ad_id':'123456789012345','clicks':300,'impressions':1200,'spend':'1500.00'}]
        url=f'/api/v1/customer/ad-metrics/?outlet_id={self.branch.pk}'
        self.assertEqual(self.client.post(url,body,format='json').status_code,200)
        self.assertEqual(self.client.post(url,body,format='json').status_code,200)
        from .models import AdMetric
        self.assertEqual(AdMetric.objects.count(),1)

    def test_analyst_returns_citable_metrics_without_credentials(self):
        from django.test import override_settings
        from .tasks import build_journey_reports
        self.send(self.batch(['page_view','add_to_cart']))
        self.client.force_authenticate(self.owner)
        report=self.client.get(f'/api/v1/customer/intelligence/?outlet_id={self.branch.pk}').data
        build_journey_reports()
        with override_settings(ANALYTICS_OPENAI_API_KEY='',ANALYTICS_OPENAI_MODEL=''):
            answer=self.client.post(f'/api/v1/customer/analyst/?outlet_id={self.branch.pk}',{'report_id':report['id'],'question':'Why zero orders?'},format='json')
        self.assertEqual(answer.status_code,200,answer.data)
        self.assertFalse(answer.data['ai_configured'])
        evidence=answer.data['data']['evidence']
        self.assertEqual(next(r['value'] for r in evidence if r['id']=='executive.orders'),0)

    def test_delayed_browser_batch_keeps_checkout_purchase_link(self):
        from .journey_services import attach_order
        order=Order.objects.create(branch=self.branch,order_number='EARLY',order_source='WEBSITE')
        attach_order(order,self.identity)
        body=self.batch(['page_view'])
        body['events'][0]['timestamp']=(timezone.now()-timedelta(seconds=10)).isoformat()
        self.send(body)
        self.assertEqual(JourneySession.objects.count(),1)
        self.assertEqual(JourneyEvent.objects.filter(order=order,name='order_success').count(),1)
        self.assertEqual(JourneySession.objects.get().session_touch['utm_source'],'facebook')


@override_settings(WEBSITE_ANALYTICS_PROVIDER='posthog')
class PostHogIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username='posthog-owner', role='RESTAURANT_OWNER')
        restaurant = Restaurant.objects.create(name='PostHog', admin=self.owner)
        self.owner.restaurant = restaurant
        self.owner.save()
        self.branch = Branch.objects.create(restaurant=restaurant, name='One', branch_code='PH1')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.config = {
            str(self.branch.pk): {
                'token': 'phc_public_project_token',
                'host': 'https://us.i.posthog.com',
                'project_url': 'https://us.posthog.com/project/12345/',
                'replay': True,
            }
        }

    def test_tracking_config_exposes_only_browser_safe_project_settings(self):
        with override_settings(POSTHOG_OUTLETS=self.config):
            response = self.client.get(f'/api/v1/customer/tracking-config/?outlet_id={self.branch.pk}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {
            'enabled': True,
            'token': 'phc_public_project_token',
            'host': 'https://us.i.posthog.com',
            'replay': True,
        })
        self.assertNotIn('project_url', response.data)

    def test_server_outbox_distinguishes_submission_confirmation_and_verified_payment(self):
        from .journey_services import attach_order, sync_order_outcomes
        from .models import PostHogDelivery, PostHogOrderIdentity
        visitor_id, session_id = uuid.uuid4(), uuid.uuid4()
        order = Order.objects.create(
            branch=self.branch,
            order_number='PH-1',
            order_source='WEBSITE',
            status='PENDING',
            total_payable=200,
        )
        context = {'visitor_id': visitor_id, 'session_id': session_id, 'posthog_session_id': session_id}

        with override_settings(POSTHOG_OUTLETS=self.config):
            attach_order(order, context)
            order.status = 'ACCEPTED'
            order.payment_status = 'PAID'
            order.paid_amount = 200
            order.save(update_fields=['status', 'payment_status', 'paid_amount'])
            sync_order_outcomes(order)

        identity = PostHogOrderIdentity.objects.get(order=order)
        self.assertEqual(identity.visitor_id, visitor_id)
        self.assertEqual(identity.session_id, session_id)
        deliveries = {row.event: row for row in PostHogDelivery.objects.filter(order=order)}
        self.assertEqual(set(deliveries), {'order_success', 'order_confirmed', 'payment_success'})
        self.assertEqual(deliveries['payment_success'].payload['properties']['distinct_id'],
                         f'outlet-{self.branch.pk}:{visitor_id}')
        self.assertEqual(deliveries['payment_success'].payload['properties']['$session_id'], str(session_id))
        self.assertEqual(deliveries['payment_success'].payload['properties']['authority'], 'server')

    def test_legacy_browser_event_ingestion_is_disabled_under_posthog(self):
        response = self.client.post('/api/v1/customer/events/', {}, format='json')
        self.assertEqual(response.status_code, 410)

    @patch('apps.customer_web.posthog_selectors.urlopen')
    def test_reporting_explains_when_server_query_key_is_not_configured(self, urlopen):
        from .posthog_selectors import reporting_overview
        filters = {'start_date': timezone.localdate(), 'end_date': timezone.localdate()}

        with override_settings(POSTHOG_OUTLETS=self.config, POSTHOG_QUERY_API_KEY=''):
            result = reporting_overview(self.branch, filters, self.owner)

        self.assertFalse(result['analytics']['visitors']['available'])
        self.assertIn('POSTHOG_QUERY_API_KEY', result['analytics']['visitors']['message'])
        urlopen.assert_not_called()

    def test_reporting_keeps_order_value_and_verified_money_in_separate_scopes(self):
        from decimal import Decimal
        from apps.orders.models import Order
        Order.objects.create(
            branch=self.branch,
            order_number='PAID-1',
            order_source='WEBSITE',
            status='ACCEPTED',
            total_payable=Decimal('200.00'),
            paid_amount=Decimal('200.00'),
            refunded_amount=Decimal('20.00'),
            payment_status='PAID',
        )
        Order.objects.create(
            branch=self.branch,
            order_number='PARTIAL-1',
            order_source='WEBSITE',
            status='ACCEPTED',
            total_payable=Decimal('100.00'),
            paid_amount=Decimal('50.00'),
            payment_status='PAID',
        )
        with override_settings(POSTHOG_OUTLETS=self.config):
            response = self.client.get(
                f'/api/v1/customer/reporting-overview/?outlet_id={self.branch.pk}'
                f'&start_date={timezone.localdate()}&end_date={timezone.localdate()}'
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['sales']['orders'], 2)
        self.assertEqual(response.data['sales']['order_value'], '300')
        self.assertEqual(response.data['sales']['paid_orders'], 1)
        self.assertEqual(response.data['sales']['received'], '250')
        self.assertEqual(response.data['sales']['refunded'], '20')
        self.assertEqual(response.data['sales']['net_received'], '230.00')
        self.assertEqual(response.data['analytics']['project_url'], self.config[str(self.branch.pk)]['project_url'])

    @patch('apps.customer_web.posthog_selectors.urlopen')
    def test_reporting_returns_aggregated_visitor_counts_and_filters_by_outlet(self, urlopen):
        from .posthog_selectors import reporting_overview
        responses = [
            {'results': [[2, 3, 5]]},
            {'results': [['2026-10-09', 2, 3, 5]]},
            {'results': [['add_to_cart', 4, 2]]},
            {'results': [['/menu', 5, 2]]},
        ]
        def response_for_query(request, timeout):
            self.assertEqual(timeout, 12)
            self.assertIn('/api/projects/12345/query/', request.full_url)
            response = BytesIO(json.dumps(responses.pop(0)).encode())
            response.status = 200
            return response

        urlopen.side_effect = response_for_query
        filters = {'start_date': timezone.localdate(), 'end_date': timezone.localdate()}

        with override_settings(POSTHOG_OUTLETS=self.config, POSTHOG_QUERY_API_KEY='phx_read_only_test_key'):
            result = reporting_overview(self.branch, filters, self.owner)

        visitor_data = result['analytics']['visitors']
        self.assertTrue(visitor_data['available'])
        self.assertEqual(visitor_data['unique_visitors'], 2)
        self.assertEqual(visitor_data['sessions'], 3)
        self.assertEqual(visitor_data['page_views'], 5)
        self.assertEqual(visitor_data['top_events'][0]['event'], 'add_to_cart')
        self.assertEqual(visitor_data['top_pages'][0]['path'], '/menu')
        self.assertEqual(urlopen.call_count, 4)
        for call in urlopen.call_args_list:
            request = call.args[0]
            self.assertIn('/api/projects/12345/query/', request.full_url)
            query = json.loads(request.data)['query']['query']
            self.assertIn("toString(properties.outlet_id) = '%s'" % self.branch.pk, query)
            self.assertIn("properties.authority = 'browser'", query)
            self.assertIn("'UTC'", query)
