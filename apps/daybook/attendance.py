"""BS calendar presentation with Gregorian storage and Nepal-local date boundaries."""
import nepali_datetime as bs
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from apps.orders.pos_services import Conflict
from apps.restaurants.models import Branch
from .models import StaffAttendance, StaffAttendanceChange
from .selectors import outlet_staff
from .serializers import today

MONTHS = ['Baisakh', 'Jestha', 'Ashadh', 'Shrawan', 'Bhadra', 'Ashwin', 'Kartik', 'Mangsir', 'Poush', 'Magh', 'Falgun', 'Chaitra']
STATUSES = ['PRESENT', 'ABSENT', 'FIRST_HALF', 'SECOND_HALF', 'UNMARKED']


class MonthQuery(serializers.Serializer):
    year = serializers.IntegerField(min_value=bs.MINYEAR, max_value=bs.MAXYEAR, required=False)
    month = serializers.IntegerField(min_value=1, max_value=12, required=False)
    search = serializers.CharField(max_length=150, allow_blank=True, default='')


class AttendanceInput(serializers.Serializer):
    staff_id = serializers.IntegerField(min_value=1)
    year = serializers.IntegerField(min_value=bs.MINYEAR, max_value=bs.MAXYEAR)
    month = serializers.IntegerField(min_value=1, max_value=12)
    day = serializers.IntegerField(min_value=1, max_value=32)
    status = serializers.ChoiceField(choices=STATUSES)
    revision = serializers.IntegerField(min_value=0)

    def validate(self, data):
        try:
            date = bs.date(data['year'], data['month'], data['day']).to_datetime_date()
        except ValueError:
            raise ValidationError('Invalid Nepali calendar date.')
        if date > today():
            raise ValidationError('Attendance cannot be marked for a future date.')
        data['date'] = date
        return data


def month_dates(year, month):
    dates = []
    for day in range(1, 33):
        try:
            dates.append(bs.date(year, month, day).to_datetime_date())
        except ValueError:
            break
    return dates


def cell_data(row):
    return {'status': row.status, 'revision': row.revision,
        'updated_at': row.updated_at.isoformat(),
        'updated_by': row.updated_by.get_full_name() or row.updated_by.username}


def month_data(branch, query):
    now = today()
    current = bs.date.from_datetime_date(now)
    year, month = query.get('year', current.year), query.get('month', current.month)
    dates = month_dates(year, month)
    records = list(StaffAttendance.objects.filter(branch=branch, date__range=(dates[0], dates[-1])).select_related('updated_by'))
    current_staff = list(outlet_staff(branch))
    ids = {user.pk for user in current_staff}
    historical = get_user_model().objects.filter(pk__in={r.staff_id for r in records}-ids).select_related('employee_profile')
    cells = {}
    for record in records:
        day = (record.date-dates[0]).days+1
        cells.setdefault(record.staff_id, {})[str(day)] = cell_data(record)
    rows = []
    search = query.get('search', '').casefold()
    for user in [*current_staff, *historical]:
        employee = getattr(user, 'employee_profile', None)
        name = employee.name if employee else (user.get_full_name() or user.username or str(user.pk))
        if search and search not in f'{name} {user.username or ""}'.casefold():
            continue
        active = user.pk in ids and user.is_active and (employee.is_active if employee else True)
        entries = cells.get(user.pk, {})
        statuses = [cell['status'] for cell in entries.values()]
        halves = statuses.count('FIRST_HALF')+statuses.count('SECOND_HALF')
        present = statuses.count('PRESENT')+halves/2
        absent = statuses.count('ABSENT')+halves/2
        rows.append({'id': user.pk, 'name': name, 'active': bool(active), 'cells': entries,
            'present': present, 'absent': absent, 'half_days': halves,
            'unmarked': sum(d <= now for d in dates)-int(present+absent)})
    return {'year': year, 'month': month, 'month_name': MONTHS[month-1], 'months': MONTHS,
        'min_year': bs.MINYEAR, 'max_year': bs.MAXYEAR,
        'today': {'year': current.year, 'month': current.month, 'day': current.day},
        'days': [{'day': index+1, 'date': date.isoformat(), 'weekday': date.strftime('%a'), 'future': date > now,
                  'today': date == now} for index, date in enumerate(dates)],
        'total_days': len(dates), 'staff': sorted(rows, key=lambda r: (r['name'].casefold(), r['id']))}


@transaction.atomic
def save_attendance(branch, actor, data):
    Branch.objects.select_for_update().get(pk=branch.pk)
    row = StaffAttendance.objects.filter(branch=branch, staff_id=data['staff_id'], date=data['date']).first()
    staff = outlet_staff(branch).filter(pk=data['staff_id'], is_active=True).first()
    employee = getattr(staff, 'employee_profile', None) if staff else None
    # Existing records remain correctable after staff leave this outlet.
    if not row and (not staff or (employee and not employee.is_active)):
        raise ValidationError({'staff_id': 'Choose active staff assigned to this outlet.'})
    if row and row.status == data['status']:
        return cell_data(row)
    if data['revision'] != (row.revision if row else 0):
        raise Conflict('Attendance changed elsewhere. Close this editor and refresh before editing again.')
    previous = row.status if row else 'UNMARKED'
    if row is None:
        row = StaffAttendance(branch=branch, staff_id=data['staff_id'], date=data['date'])
    row.status = data['status']
    row.revision += 1
    row.updated_by = actor
    row.save()
    StaffAttendanceChange.objects.create(attendance=row, previous_status=previous, status=row.status, recorded_by=actor)
    return cell_data(row)
