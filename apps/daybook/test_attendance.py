from datetime import date, timedelta
from unittest.mock import patch
import nepali_datetime as bs
from django.test import TestCase
from rest_framework.test import APIClient
from apps.orders import test_pos
from apps.user_accounts.models import User
from .attendance import month_dates
from .models import StaffAttendance, StaffAttendanceChange


class AttendanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_pos.PosWorkflowTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.clock = patch('apps.daybook.attendance.today', return_value=date(2026, 10, 15))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.current = bs.date.from_datetime_date(date(2026, 10, 15))
        self.client = APIClient()
        self.client.force_authenticate(self.manager)
        self.url = f'/api/v1/daybook/attendance/?outlet_id={self.branch.pk}'

    def save(self, day=1, status='PRESENT', revision=0, **kwargs):
        return self.client.post(self.url, {'staff_id':self.cashier.pk, 'year':self.current.year,
            'month':self.current.month, 'day':day, 'status':status, 'revision':revision, **kwargs}, format='json')

    def test_bs_calendar_default_and_month_lengths(self):
        self.assertEqual(bs.date.from_datetime_date(date(2018, 11, 7)), bs.date(2075, 7, 21))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['today'], {'year':self.current.year,'month':self.current.month,'day':self.current.day})
        self.assertEqual(response.data['days'][self.current.day-1]['date'], '2026-10-15')
        self.assertTrue(response.data['days'][self.current.day-1]['today'])
        for month in range(1, 13):
            dates = month_dates(2082, month)
            next_month = bs.date(2082+(month==12), month%12+1, 1).to_datetime_date()
            self.assertEqual(dates[-1]+timedelta(days=1), next_month)
            result = self.client.get(self.url, {'year':2082,'month':month})
            self.assertEqual(result.data['total_days'], len(dates))
        self.assertEqual(self.client.get(self.url, {'year':1}).status_code, 400)
        self.assertEqual(self.client.get(self.url, {'month':13}).status_code, 400)

    def test_mark_edit_half_days_retry_audit_and_search(self):
        for day,status in enumerate(['PRESENT','ABSENT','FIRST_HALF','SECOND_HALF'],1):
            response = self.save(day,status)
            self.assertEqual(response.status_code, 200, response.data)
        data = self.client.get(self.url, {'search':'pos-cashier'}).data
        self.assertEqual(len(data['staff']), 1)
        staff = data['staff'][0]
        self.assertEqual((staff['present'],staff['absent'],staff['half_days']), (2,2,2))
        self.assertEqual(staff['unmarked'], self.current.day-4)
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(StaffAttendanceChange.objects.count(), 4)
        self.assertEqual(self.save(status='ABSENT').status_code, 409)
        self.assertEqual(self.save(status='ABSENT',revision=1).status_code, 200)
        self.assertEqual(self.save(status='UNMARKED',revision=2).status_code, 200)
        self.assertEqual(StaffAttendance.objects.count(), 4)
        self.assertEqual(StaffAttendanceChange.objects.count(), 6)
        history = StaffAttendanceChange.objects.order_by('pk').last()
        self.assertEqual((history.previous_status,history.status), ('ABSENT','UNMARKED'))

    def test_outlet_permissions_dates_and_historical_records(self):
        outsider = User.objects.create(username='att-other', role='CASHIER', branch=self.other)
        self.assertEqual(self.save(staff_id=outsider.pk).status_code, 400)
        tomorrow = self.current+timedelta(days=1)
        self.assertEqual(self.save(year=tomorrow.year,month=tomorrow.month,day=tomorrow.day).status_code, 400)
        self.assertEqual(self.save(month=12,day=32).status_code, 400)
        self.assertEqual(self.save(status='UNKNOWN').status_code, 400)
        self.assertEqual(self.save().status_code, 200)
        self.cashier.branch = self.other
        self.cashier.save()
        data = self.client.get(self.url, {'search':'pos-cashier'}).data
        self.assertFalse(data['staff'][0]['active'])
        self.assertEqual(self.save(day=2).status_code, 400)
        self.assertEqual(self.save(status='ABSENT',revision=1).status_code, 200)
        self.client.force_authenticate(self.owner)
        other = self.client.get(f'/api/v1/daybook/attendance/?outlet_id={self.other.pk}').data
        self.assertTrue(all(not row['cells'] for row in other['staff']))
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.save().status_code, 403)
