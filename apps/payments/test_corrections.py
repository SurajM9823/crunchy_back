from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from apps.orders import test_pos
from apps.orders.models import Order, PosReceipt
from apps.orders.pos_selectors import order_data, order_queryset
from apps.orders.pos_services import record_tenders
from apps.daybook.models import DaybookEntry
from apps.daybook.serializers import today
from apps.daybook.selectors import ledger
from apps.daybook.serializers import LedgerQuery
from apps.inventory.models import PurchaseInvoice, Supplier, SupplierPayment
from apps.customer_web.models import CustomerCollection, CustomerContact
from .models import PaymentTransaction, PaymentMethodCorrection


class PaymentCorrectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_pos.PosWorkflowTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client=APIClient(); self.client.force_authenticate(self.manager)
        self.order=Order.objects.create(branch=self.branch,order_number='CORRECT',is_pos_managed=True,
            total_payable=100,paid_amount=100,payment_method='CASH',payment_status='PAID')
        self.payment=PaymentTransaction.objects.create(order=self.order,branch=self.branch,transaction_id='CORRECT-PAY',
            amount=100,payment_method='ESEWA',status='SUCCESS')
        self.entry=DaybookEntry.objects.create(branch=self.branch,date=today(),direction='IN',amount=100,
            payment_method='ESEWA',category='Sales',description='Sale',payment=self.payment,recorded_by=self.manager,source='SALE')

    def correct(self,kind='payment',target='CORRECT-PAY',expected='ESEWA',method='CASH',branch=None):
        return self.client.post(f'/api/v1/payments/correct-method/?outlet_id={(branch or self.branch).pk}',
            {'kind':kind,'target':str(target),'expected_method':expected,'method':method,'reason':'Wrong method selected'},format='json')

    def test_legacy_display_and_settlement_summary(self):
        data=order_data(order_queryset(self.branch).get(pk=self.order.pk))
        self.assertEqual(data['payment_method'],'ESEWA')
        # A fresh order settles using its actual tender rather than its default cash field.
        fresh=Order.objects.create(branch=self.branch,order_number='FRESH',total_payable=50,is_pos_managed=True)
        record_tenders(fresh,[{'method':'ESEWA','amount':Decimal('50')}],self.manager)
        fresh.save()
        self.assertEqual(fresh.payment_method,'ESEWA')

    def test_correction_updates_payment_daybook_receipt_and_keeps_amounts(self):
        receipt=PosReceipt.objects.create(order=self.order,number='BILL-CORRECT',kind='BILL',
            snapshot=order_data(order_queryset(self.branch).get(pk=self.order.pk)))
        result=self.correct()
        self.assertEqual(result.status_code,200,result.data)
        self.payment.refresh_from_db();self.entry.refresh_from_db();self.order.refresh_from_db();receipt.refresh_from_db()
        self.assertEqual(self.payment.payment_method,'CASH')
        self.assertEqual(self.entry.payment_method,'CASH')
        self.assertEqual(self.order.payment_method,'CASH')
        self.assertEqual(receipt.snapshot['payments'][0]['method'],'CASH')
        self.assertEqual(receipt.snapshot['payment_method'],'CASH')
        self.assertEqual((self.payment.amount,self.order.paid_amount,self.order.total_payable),(100,100,100))
        query=LedgerQuery(data={});query.is_valid(raise_exception=True)
        self.assertEqual(ledger(self.branch,query.validated_data,True)['summary']['cash_in'],'100.00')
        self.assertEqual(self.correct().status_code,200)
        self.assertEqual(PaymentMethodCorrection.objects.count(),1)
        self.assertEqual(self.correct(method='CARD').status_code,409)

    def test_daybook_payment_and_supplier_links(self):
        result=self.correct(kind='daybook',target=self.entry.pk,method='CARD')
        self.assertEqual(result.status_code,200,result.data)
        self.payment.refresh_from_db();self.assertEqual(self.payment.payment_method,'CARD')
        supplier=Supplier.objects.create(id='vendor',branch=self.branch,name='Vendor')
        entry=DaybookEntry.objects.create(branch=self.branch,date=today(),direction='OUT',amount=10,
            payment_method='CASH',category='Supplier',description='Payment',recorded_by=self.manager,source='SUPPLIER')
        payment=SupplierPayment.objects.create(supplier=supplier,amount=10,date=today(),method='CASH',daybook_entry=entry,recorded_by=self.manager)
        result=self.correct(kind='daybook',target=entry.pk,expected='CASH',method='FONEPAY')
        self.assertEqual(result.status_code,200,result.data)
        payment.refresh_from_db();self.assertEqual(payment.method,'FONEPAY')

    def test_purchase_method_only_and_outlet_permissions(self):
        purchase=PurchaseInvoice.objects.create(id='purchase-test',branch=self.branch,invoice_number='INV-CORRECT',
            total_amount=100,initial_paid_amount=40,paid_amount=40,due_amount=60,payment_method='CASH')
        result=self.correct(kind='purchase',target=purchase.pk,expected='CASH',method='FONEPAY')
        self.assertEqual(result.status_code,200,result.data)
        purchase.refresh_from_db()
        self.assertEqual((purchase.payment_method,purchase.paid_amount,purchase.due_amount),('FONEPAY',40,60))
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.correct(branch=self.other).status_code,404)
        self.client.force_authenticate(self.chef)
        self.assertEqual(self.correct().status_code,403)
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.correct(method='CREDIT').status_code,400)
        self.entry.voided_at=self.entry.created_at;self.entry.save()
        self.assertEqual(self.correct(kind='daybook',target=self.entry.pk).status_code,400)

    def test_split_payment_and_customer_collection(self):
        contact=CustomerContact.objects.create(branch=self.branch,name='Customer',phone='9800000001')
        collection=CustomerCollection.objects.create(contact=contact,amount=100,method='ESEWA',date=today(),recorded_by=self.manager,snapshot={'method':'ESEWA'})
        self.payment.raw_response={'customer_collection_id':collection.pk};self.payment.save()
        PaymentTransaction.objects.create(order=self.order,branch=self.branch,transaction_id='SECOND',amount=10,payment_method='ESEWA',status='SUCCESS',raw_response={'customer_collection_id':collection.pk})
        result=self.correct()
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['order']['payment_method'],'SPLIT')
        collection.refresh_from_db()
        self.assertEqual(collection.method,'SPLIT')
        self.assertEqual(collection.snapshot['method'],'SPLIT')
