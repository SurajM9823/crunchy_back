import os
import sys
import django

# Setup Django Environment
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from decimal import Decimal
from apps.restaurants.models import Restaurant
from apps.catalog.models import Category, Product

def seed_catalog():
    print("🚀 Seeding CrunchyBag Catalog (5 Categories & 15+ Products)...")

    restaurant = Restaurant.objects.first()
    if not restaurant:
        print("❌ Error: No Restaurant found in database. Please run migrations/seed restaurant first.")
        return

    # 1. DEFINE 5 FEATURED CATEGORIES
    categories_data = [
        {
            "id": "cat-crispy-burgers",
            "name": "Crispy Burgers",
            "icon_name": "Flame",
            "display_order": 1,
        },
        {
            "id": "cat-fried-chicken",
            "name": "Fried Chicken & Wings",
            "icon_name": "Drumstick",
            "display_order": 2,
        },
        {
            "id": "cat-bag-combos",
            "name": "Crunchy Bag Combos",
            "icon_name": "Package",
            "display_order": 3,
        },
        {
            "id": "cat-crunchy-sides",
            "name": "Sides & Finger Foods",
            "icon_name": "Pizza",
            "display_order": 4,
        },
        {
            "id": "cat-drinks-shakes",
            "name": "Drinks & Cold Shakes",
            "icon_name": "Coffee",
            "display_order": 5,
        },
    ]

    categories = {}
    for cdata in categories_data:
        cat, created = Category.objects.update_or_create(
            id=cdata["id"],
            defaults={
                "restaurant": restaurant,
                "name": cdata["name"],
                "icon_name": cdata["icon_name"],
                "display_order": cdata["display_order"],
                "is_archived": False,
            }
        )
        categories[cdata["id"]] = cat
        print(f"  ✓ Category: {cat.name} ({'Created' if created else 'Updated'})")

    # 2. DEFINE 15+ PRODUCTS WITH REAL FOOD IMAGES
    products_data = [
        # Category 1: Crispy Burgers (3 Items)
        {
            "id": "prod-crunchy-classic",
            "category_id": "cat-crispy-burgers",
            "name": "Crunchy Classic Chicken Burger",
            "description": "Double crispy fried chicken thigh, melted cheddar cheese, house pickles, and signature mayo sauce in toasted brioche bun.",
            "base_price": Decimal("450.00"),
            "prep_time_minutes": 10,
            "calories": 620,
            "dietary_tags": ["Popular", "Chef's Choice"],
            "images": [
                "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?auto=format&fit=crop&w=600&q=80",
                "https://images.unsplash.com/photo-1550547660-d9450f859349?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-spicy-zester",
            "category_id": "cat-crispy-burgers",
            "name": "Spicy Zester Double Burger",
            "description": "Ghost pepper glazed crispy chicken patty, jalapeno slices, spicy aioli, and shredded iceberg lettuce.",
            "base_price": Decimal("520.00"),
            "prep_time_minutes": 12,
            "calories": 680,
            "dietary_tags": ["Spicy", "Popular"],
            "images": [
                "https://images.unsplash.com/photo-1586190848861-99aa4a171e90?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-smoky-bbq-bacon",
            "category_id": "cat-crispy-burgers",
            "name": "Smoky BBQ Bacon Melt Burger",
            "description": "Smokey BBQ sauce, smoked beef bacon strips, double cheddar melt, crispy onion rings, and toasted potato bun.",
            "base_price": Decimal("590.00"),
            "prep_time_minutes": 14,
            "calories": 740,
            "dietary_tags": ["Chef's Choice"],
            "images": [
                "https://images.unsplash.com/photo-1553979459-d2229ba7433b?auto=format&fit=crop&w=600&q=80"
            ],
        },

        # Category 2: Fried Chicken & Wings (3 Items)
        {
            "id": "prod-crispy-tender-bucket",
            "category_id": "cat-fried-chicken",
            "name": "Golden Crispy Tenders (6 Pcs)",
            "description": "Hand-breaded buttermilk chicken breast tenders fried to golden perfection with honey mustard dip.",
            "base_price": Decimal("480.00"),
            "prep_time_minutes": 10,
            "calories": 520,
            "dietary_tags": ["Popular", "Halal"],
            "images": [
                "https://images.unsplash.com/photo-1626645738196-c2a7c87a8f58?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-buffalo-hot-wings",
            "category_id": "cat-fried-chicken",
            "name": "Fiery Buffalo Wings (8 Pcs)",
            "description": "Juicy jumbo wings tossed in authentic fiery cayenne pepper buffalo sauce with ranch drizzle.",
            "base_price": Decimal("550.00"),
            "prep_time_minutes": 12,
            "calories": 610,
            "dietary_tags": ["Spicy"],
            "images": [
                "https://images.unsplash.com/photo-1567620832903-9fc6debc209f?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-honey-garlic-wings",
            "category_id": "cat-fried-chicken",
            "name": "Honey Soy Garlic Wings (8 Pcs)",
            "description": "Sticky glazed chicken wings tossed in garlic soy reduction with toasted sesame seeds and fresh scallions.",
            "base_price": Decimal("560.00"),
            "prep_time_minutes": 12,
            "calories": 590,
            "dietary_tags": ["Chef's Choice"],
            "images": [
                "https://images.unsplash.com/photo-1527477396000-e27163b481c2?auto=format&fit=crop&w=600&q=80"
            ],
        },

        # Category 3: Crunchy Bag Combos (3 Items)
        {
            "id": "prod-single-crunchy-box",
            "category_id": "cat-bag-combos",
            "name": "Solo Crunchy Feast Box",
            "description": "1 Crunchy Classic Burger + 2 Crispy Tenders + Regular Seasoned Fries + 1 Chilled Soda.",
            "base_price": Decimal("750.00"),
            "prep_time_minutes": 15,
            "calories": 1150,
            "dietary_tags": ["Best Value", "Popular"],
            "images": [
                "https://images.unsplash.com/photo-1610614819513-58e34989848b?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-duo-partner-combo",
            "category_id": "cat-bag-combos",
            "name": "Duo Partner Crunchy Bag",
            "description": "2 Crispy Burgers + 6 Hot Wings + Large Garlic Fries + 2 Chilled Soda Bottles.",
            "base_price": Decimal("1350.00"),
            "prep_time_minutes": 18,
            "calories": 1980,
            "dietary_tags": ["Best Value"],
            "images": [
                "https://images.unsplash.com/photo-1594212699903-ec8a3eca50f6?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-family-party-bucket",
            "category_id": "cat-bag-combos",
            "name": "Family Party Mega Bucket",
            "description": "12 Pcs Crispy Fried Chicken + 8 Hot Wings + 2 Large Fries + Cole Slaw + 1.25L Soda.",
            "base_price": Decimal("2450.00"),
            "prep_time_minutes": 22,
            "calories": 3400,
            "dietary_tags": ["Family Pack"],
            "images": [
                "https://images.unsplash.com/photo-1513104890138-7c749659a591?auto=format&fit=crop&w=600&q=80"
            ],
        },

        # Category 4: Sides & Finger Foods (3 Items)
        {
            "id": "prod-peri-peri-fries",
            "category_id": "cat-crunchy-sides",
            "name": "Loaded Peri Peri Seasoned Fries",
            "description": "Skin-on thick potato cut fries tossed in tangy Peri Peri spice mix served with garlic mayo.",
            "base_price": Decimal("220.00"),
            "prep_time_minutes": 8,
            "calories": 380,
            "dietary_tags": ["Vegetarian", "Popular"],
            "images": [
                "https://images.unsplash.com/photo-1573080496219-bb080dd4f877?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-mozzarella-cheese-sticks",
            "category_id": "cat-crunchy-sides",
            "name": "Crispy Mozzarella Sticks (5 Pcs)",
            "description": "Golden crusted string mozzarella cheese sticks served hot with rich marinara dip.",
            "base_price": Decimal("320.00"),
            "prep_time_minutes": 8,
            "calories": 410,
            "dietary_tags": ["Vegetarian"],
            "images": [
                "https://images.unsplash.com/photo-1531749668029-2db88e4276c7?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-onion-rings-basket",
            "category_id": "cat-crunchy-sides",
            "name": "Beer Battered Onion Rings Basket",
            "description": "Thick sweet Spanish onion rings coated in crispy batter fried golden crisp with chipotle dip.",
            "base_price": Decimal("260.00"),
            "prep_time_minutes": 7,
            "calories": 340,
            "dietary_tags": ["Vegetarian"],
            "images": [
                "https://images.unsplash.com/photo-1639024471283-03518883512d?auto=format&fit=crop&w=600&q=80"
            ],
        },

        # Category 5: Drinks & Cold Shakes (3 Items)
        {
            "id": "prod-chocolate-fudge-shake",
            "category_id": "cat-drinks-shakes",
            "name": "Thick Chocolate Fudge Milkshake",
            "description": "Rich Belgian chocolate ice cream blended with dark chocolate fudge sauce and whipped cream topping.",
            "base_price": Decimal("290.00"),
            "prep_time_minutes": 5,
            "calories": 480,
            "dietary_tags": ["Popular", "Vegetarian"],
            "images": [
                "https://images.unsplash.com/photo-1572490122747-3968b75cc699?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-oreo-cookies-shake",
            "category_id": "cat-drinks-shakes",
            "name": "Oreo Cookies & Cream Shake",
            "description": "Creamy vanilla ice cream blended with crushed Oreo cookies and Hershey chocolate drizzle.",
            "base_price": Decimal("310.00"),
            "prep_time_minutes": 5,
            "calories": 510,
            "dietary_tags": ["Popular"],
            "images": [
                "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?auto=format&fit=crop&w=600&q=80"
            ],
        },
        {
            "id": "prod-fresh-lemon-mojito",
            "category_id": "cat-drinks-shakes",
            "name": "Refreshing Mint Lemon Mojito",
            "description": "Fresh crushed mint leaves, lime juice, brown sugar, and chilled sparkling soda over ice.",
            "base_price": Decimal("220.00"),
            "prep_time_minutes": 4,
            "calories": 160,
            "dietary_tags": ["Vegetarian"],
            "images": [
                "https://images.unsplash.com/photo-1513558161293-cdaf765ed2fd?auto=format&fit=crop&w=600&q=80"
            ],
        },
    ]

    for pdata in products_data:
        cat = categories[pdata["category_id"]]
        prod, created = Product.objects.update_or_create(
            id=pdata["id"],
            defaults={
                "category": cat,
                "name": pdata["name"],
                "description": pdata["description"],
                "base_price": pdata["base_price"],
                "cost_price": Decimal(str(float(pdata["base_price"]) * 0.45)),
                "prep_time_minutes": pdata["prep_time_minutes"],
                "calories": pdata["calories"],
                "dietary_tags": pdata["dietary_tags"],
                "images": pdata["images"],
                "main_image_index": 0,
                "is_available": True,
                "is_delivery_eligible": True,
                "is_web_visible": True,
                "is_archived": False,
            }
        )
        print(f"  ✓ Product: {prod.name} (NPR {prod.base_price}) [{'Created' if created else 'Updated'}]")

    print(f"\n✅ Catalog Seeding Complete! Inserted/Updated {len(products_data)} products across 5 categories.")

if __name__ == "__main__":
    seed_catalog()
