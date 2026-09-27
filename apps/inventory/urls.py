from django.urls import path
from .views import (
     InventoryItemListCreateAPIView,
     InventoryItemDetailAPIView,
     InventoryRestockAPIView,
     SupplierListCreateAPIView,
     InventoryCategoryListCreateAPIView,
     PurchaseInvoiceListCreateAPIView,
     PurchaseInvoiceDetailAPIView,
     InventoryAuditReconcileAPIView,
     StockMovementLedgerListAPIView,
     LowStockAlertAPIView,
     RecipeListCreateAPIView,
     StockTransactionHistoryAPIView,
)

urlpatterns = [
    # Stock Items Catalog & Restock
    path('items/', InventoryItemListCreateAPIView.as_view(), name='inventory-item-list-create'),
    path('items/<int:item_id>/', InventoryItemDetailAPIView.as_view(), name='inventory-item-detail'),
    path('items/<int:item_id>/restock/', InventoryRestockAPIView.as_view(), name='inventory-item-restock'),

    # Suppliers (Select2 Search & Auto-create)
    path('suppliers/', SupplierListCreateAPIView.as_view(), name='inventory-supplier-list-create'),

    # Categories (Select2 Search & Auto-create)
    path('categories/', InventoryCategoryListCreateAPIView.as_view(), name='inventory-category-list-create'),

    # Inward Purchase Invoices & Restocking
    path('purchases/', PurchaseInvoiceListCreateAPIView.as_view(), name='inventory-purchase-list-create'),
    path('purchases/<str:purchase_id>/', PurchaseInvoiceDetailAPIView.as_view(), name='inventory-purchase-detail'),

    # Physical Stock Count Audit & Variance Reconciliation
    path('audits/reconcile/', InventoryAuditReconcileAPIView.as_view(), name='inventory-audit-reconcile'),

    # Stock Movement Ledger (Inward Restocks, Sales Deductions, Audit Adjustments)
    path('movements/', StockMovementLedgerListAPIView.as_view(), name='inventory-movement-ledger'),

    # Low Stock Alert
    path('low-stock/', LowStockAlertAPIView.as_view(), name='inventory-low-stock'),

    # Recipe Bill of Materials
    path('recipes/', RecipeListCreateAPIView.as_view(), name='inventory-recipe-list-create'),

    # Audit Ledger Transactions (Legacy & BOM)
    path('transactions/', StockTransactionHistoryAPIView.as_view(), name='inventory-transactions'),
]
