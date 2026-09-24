from decimal import Decimal
from django.test import TestCase
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework import status

from apps.user_accounts.models import UserRole
from apps.user_accounts.services import user_create
from apps.restaurants.models import OperateType
from apps.restaurants.services import restaurant_create, branch_with_admin_create
from apps.catalog.services import category_create, product_create
from apps.orders.models import FulfillmentType, OrderSource, OrderStatus, PaymentStatus
from apps.orders.services import order_create_or_append_tab
from .models import (
    DeliveryZone,
    RiderProfile,
    DeliveryDispatch,
    DispatchStatus,
    RiderStatus,
    VehicleType,
)
from .services import (
    delivery_zone_create,
    rider_profile_create,
    order_dispatch_create,
    assign_rider_to_delivery,
    rider_update_dispatch_status,
)
from .geo_tracker import haversine_distance_km, get_rider_live_location, update_rider_live_location
from .selectors import list_zones_for_branch, list_riders_for_branch, list_active_dispatches_for_branch


class DeliveryEngineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

        # 1. Setup Brand & Branch
        self.owner = user_create(
            username="delivery_owner",
            email="owner@delivery.local",
            phone_number="+9779800000071",
            password="OwnerPassword123!",
            role=UserRole.RESTAURANT_OWNER,
            is_staff=True,
        )
        self.brand = restaurant_create(
            name="Crunchy Bag Fleet Brand",
            admin_user=self.owner,
        )
        self.branch, self.manager = branch_with_admin_create(
            restaurant=self.brand,
            name="Thamel Flagship Hub",
            branch_code="CB-THM-01",
            operate_type=OperateType.DINE_IN,
            admin_username="thamel_mgr",
            admin_phone="+9779800000072",
            admin_password="ManagerPass123!",
        )

        # 2. Setup Rider User & Profile
        self.rider_user = user_create(
            username="fleet_rider_bikash",
            email="bikash@crunchybag.com",
            phone_number="+9779811112233",
            password="RiderPassword123!",
            role=UserRole.RIDER,
            branch=self.branch,
        )
        self.rider_profile = rider_profile_create(
            user=self.rider_user,
            branch=self.branch,
            vehicle_type=VehicleType.MOTORCYCLE,
            vehicle_plate="BA 85 PA 4521",
        )

        # 3. Setup Catalog Item for Ordering
        self.category = category_create(
            name="Burgers",
            id="cat-del-burgers",
        )
        self.burger = product_create(
            category=self.category,
            name="Crunchy Smash Burger",
            id="prod-smash-01",
            base_price=Decimal('420.00'),
            requires_kitchen=True,
        )

        # 4. Setup Delivery Zone (Thamel 5km Radius Hub: Lat 27.7154, Lng 85.3123)
        self.zone = delivery_zone_create(
            branch=self.branch,
            name="Thamel & Central Kathmandu",
            center_lat=Decimal('27.715400'),
            center_lng=Decimal('85.312300'),
            radius_km=Decimal('5.00'),
            delivery_fee=Decimal('60.00'),
            min_order_amount=Decimal('200.00'),
            free_delivery_threshold=Decimal('1500.00'),
            estimated_delivery_minutes=30,
        )

    def test_haversine_distance_calculation(self):
        """
        Verify pure-python Haversine distance formula accuracy.
        Thamel (27.7154, 85.3123) to Durbar Marg (27.7118, 85.3175) is ~0.65 km.
        """
        dist = haversine_distance_km(27.7154, 85.3123, 27.7118, 85.3175)
        self.assertGreater(dist, 0.5)
        self.assertLess(dist, 0.9)

    def test_delivery_zone_listing_and_creation_api(self):
        """
        Manager can list delivery zones and define new delivery boundary zones via API.
        """
        self.client.force_authenticate(user=self.manager)

        # List existing zones
        response = self.client.get('/api/v1/delivery/zones/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], "Thamel & Central Kathmandu")

        # Create new zone
        payload = {
            "name": "Patan & Lalitpur Hub",
            "center_lat": "27.674400",
            "center_lng": "85.324000",
            "radius_km": "6.00",
            "delivery_fee": "80.00",
            "min_order_amount": "300.00",
            "free_delivery_threshold": "2000.00",
            "estimated_delivery_minutes": 40,
        }
        res_create = self.client.post('/api/v1/delivery/zones/', data=payload, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_create.data['name'], "Patan & Lalitpur Hub")
        self.assertEqual(DeliveryZone.objects.filter(branch=self.branch).count(), 2)

    def test_rider_fleet_listing_api(self):
        """
        Branch manager can view active fleet riders and their statuses.
        """
        self.client.force_authenticate(user=self.manager)
        response = self.client.get('/api/v1/delivery/riders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['vehicle_plate'], "BA 85 PA 4521")
        self.assertEqual(response.data[0]['status'], RiderStatus.AVAILABLE)

    def test_ephemeral_gps_ping_in_redis(self):
        """
        Rider mobile app transmits high-frequency GPS telemetry into Redis cache (60s TTL).
        Verify coordinates are stored ephemerally without PostgreSQL write lock thrashing.
        """
        self.client.force_authenticate(user=self.rider_user)
        ping_payload = {
            "lat": 27.714500,
            "lng": 85.313200,
            "heading": 180.5,
            "speed_kmh": 32.4,
        }
        response = self.client.post('/api/v1/delivery/rider/location/', data=ping_payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], "ping_received")

        # Verify Redis cached telemetry
        loc = get_rider_live_location(self.rider_profile.id)
        self.assertIsNotNone(loc)
        self.assertEqual(loc['lat'], 27.7145)
        self.assertEqual(loc['lng'], 85.3132)
        self.assertEqual(loc['speed_kmh'], 32.4)

    def test_full_delivery_dispatch_lifecycle(self):
        """
        End-to-End Delivery Flow:
        1. Order created with DELIVERY fulfillment.
        2. Dispatch created with customer coordinates (auto-matched to Thamel zone).
        3. Manager assigns rider (Rider marked BUSY).
        4. Rider transitions to PICKED_UP (Order marked OUT_FOR_DELIVERY).
        5. Rider transitions to DELIVERED (Order COMPLETED, Paid, Rider AVAILABLE, total_deliveries=1).
        """
        # 1. Create Delivery Order
        order = order_create_or_append_tab(
            branch=self.branch,
            raw_items=[{'product_id': self.burger.id, 'quantity': 2, 'notes': 'Extra crispy'}],
            fulfillment_type=FulfillmentType.DELIVERY,
            order_source=OrderSource.WEBSITE,
            customer_name="Aayush Shrestha",
            customer_phone="+9779841234567",
            delivery_address="Lazimpat, Kathmandu (Near Hotel Ambassador)",
        )
        self.assertEqual(order.fulfillment_type, FulfillmentType.DELIVERY)
        self.assertEqual(order.status, OrderStatus.PENDING)

        # 2. Create Delivery Dispatch with coordinates inside Thamel Zone (Lazimpat: ~1.2 km away)
        dispatch = order_dispatch_create(
            order=order,
            delivery_address="Lazimpat, Kathmandu",
            destination_lat=Decimal('27.721500'),
            destination_lng=Decimal('85.318200'),
        )
        self.assertEqual(dispatch.status, DispatchStatus.UNASSIGNED)
        self.assertEqual(dispatch.zone, self.zone)  # Auto-matched zone!

        # 3. Manager assigns Rider
        self.client.force_authenticate(user=self.manager)
        assign_res = self.client.post(
            f'/api/v1/delivery/dispatches/{dispatch.id}/assign/',
            data={'rider_id': self.rider_profile.id},
            format='json',
        )
        self.assertEqual(assign_res.status_code, status.HTTP_200_OK)
        self.assertEqual(assign_res.data['status'], DispatchStatus.ASSIGNED)
        self.assertEqual(assign_res.data['rider_plate'], "BA 85 PA 4521")

        self.rider_profile.refresh_from_db()
        self.assertEqual(self.rider_profile.status, RiderStatus.BUSY)

        # 4. Rider marks order PICKED_UP (Out for delivery)
        self.client.force_authenticate(user=self.rider_user)
        pickup_res = self.client.post(
            f'/api/v1/delivery/dispatches/{dispatch.id}/status/',
            data={'status': DispatchStatus.PICKED_UP, 'rider_notes': 'Picked up hot from kitchen'},
            format='json',
        )
        self.assertEqual(pickup_res.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.OUT_FOR_DELIVERY)

        # 5. Public Customer Live Tracking Endpoint
        anon_client = APIClient()
        track_by_id = anon_client.get(f'/api/v1/delivery/dispatches/{order.id}/track/')
        self.assertEqual(track_by_id.status_code, status.HTTP_200_OK)
        self.assertEqual(track_by_id.data['status'], DispatchStatus.PICKED_UP)
        self.assertEqual(track_by_id.data['customer_name'], "Aayush Shrestha")

        track_by_order_num = anon_client.get(f'/api/v1/delivery/dispatches/{order.order_number}/track/')
        self.assertEqual(track_by_order_num.status_code, status.HTTP_200_OK)
        self.assertEqual(track_by_order_num.data['dispatch_id'], dispatch.dispatch_id)

        # 6. Rider marks DELIVERED
        deliver_res = self.client.post(
            f'/api/v1/delivery/dispatches/{dispatch.id}/status/',
            data={'status': DispatchStatus.DELIVERED, 'rider_notes': 'Handed over to customer'},
            format='json',
        )
        self.assertEqual(deliver_res.status_code, status.HTTP_200_OK)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.COMPLETED)
        self.assertEqual(order.payment_status, PaymentStatus.PAID)

        self.rider_profile.refresh_from_db()
        self.assertEqual(self.rider_profile.status, RiderStatus.AVAILABLE)
        self.assertEqual(self.rider_profile.total_deliveries, 1)
