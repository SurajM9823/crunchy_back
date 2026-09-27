from django.urls import path
from .views import (
    InventoryItemListCreateAPIView,
    InventoryItemDetailAPIView,
    InventoryRestockAPIView,
    LowStockAlertAPIView,
    RecipeListCreateAPIView,
    StockTransactionHistoryAPIView,
)

urlpatterns = [
    # Stock Items CRUD
    path('items/', InventoryItemListCreateAPIView.as_view(), name='inventory-item-list-create'),
    path('items/<int:item_id>/', InventoryItemDetailAPIView.as_view(), name='inventory-item-detail'),
    path('items/<int:item_id>/restock/', InventoryRestockAPIView.as_view(), name='inventory-item-restock'),

    # Low Stock Alert
    path('low-stock/', LowStockAlertAPIView.as_view(), name='inventory-low-stock'),

    # Recipe Bill of Materials
    path('recipes/', RecipeListCreateAPIView.as_view(), name='inventory-recipe-list-create'),

    # Audit Ledger Transactions
    path('transactions/', StockTransactionHistoryAPIView.as_view(), name='inventory-transactions'),
]

