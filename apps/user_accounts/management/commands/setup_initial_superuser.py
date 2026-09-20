from django.core.management.base import BaseCommand
from apps.user_accounts.models import User
from apps.user_accounts.services import superuser_create


class Command(BaseCommand):
    help = "Creates an initial superuser for Crunchy RMS with phone, email, and username credentials"

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, default='admin', help='Superuser username')
        parser.add_argument('--email', type=str, default='admin@crunchy.local', help='Superuser email')
        parser.add_argument('--phone', type=str, default='+15550192831', help='Superuser phone number')
        parser.add_argument('--password', type=str, default='Admin@123456', help='Superuser password')

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']
        phone = options['phone']
        password = options['password']

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING(f"Superuser '{username}' already exists."))
            return

        superuser = superuser_create(
            username=username,
            email=email,
            phone_number=phone,
            password=password,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Successfully created initial superuser '{superuser.username}'!\n"
            f"  - Username: {superuser.username}\n"
            f"  - Email:    {superuser.email}\n"
            f"  - Phone:    {superuser.phone_number}\n"
            f"  - Password: {password}\n"
            f"You can now log in using any of the three identifiers at /superuser/login/ or /admin/."
        ))

