import re
from django.db.models import Q
from .models import CustomerContact


def matching_customers(branch, query):
    query = query.strip()[:100]
    rows = CustomerContact.objects.filter(branch=branch)
    if query:
        match = Q(name__icontains=query) | Q(phone__icontains=query)
        digits = re.sub(r'\D', '', query)
        if digits and re.fullmatch(r'[+\d\s().-]+', query):
            match |= Q(phone__icontains=digits)
        rows = rows.filter(match)
    return list(rows.order_by('-last_seen', 'pk').values('id', 'name', 'phone')[:25])
