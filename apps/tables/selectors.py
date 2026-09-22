from .models import DiningTable


def list_tables_by_branch(branch_id: int, active_only: bool = False):
    qs = DiningTable.objects.filter(branch_id=branch_id)
    if active_only:
        qs = qs.filter(is_active=True)
    return qs.order_by('section', 'table_number')


def get_table_by_id(table_id: int) -> DiningTable:
    return (
        DiningTable.objects
        .select_related('branch', 'branch__restaurant')
        .filter(id=table_id)
        .first()
    )


def get_table_by_number(branch_id: int, table_number: str) -> DiningTable:
    return (
        DiningTable.objects
        .select_related('branch')
        .filter(branch_id=branch_id, table_number=table_number.strip())
        .first()
    )

