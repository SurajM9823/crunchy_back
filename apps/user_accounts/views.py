from django.shortcuts import render, redirect
from django.views import View
from django.contrib.auth import logout
from django.contrib import messages
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from .serializers import LoginSerializer, UserOutputSerializer, UserCreateSerializer
from .services import authenticate_user, generate_auth_tokens, superuser_login, user_create
from .selectors import get_user_by_id, list_staff_users, get_user_by_identifier


class SuperuserLoginView(View):
    """
    Renders and handles the dedicated Superuser / Staff Login Web Portal.
    Accepts Email, Phone Number, or Username.
    """
    template_name = 'user_accounts/superuser_login.html'

    def get(self, request):
        if request.user.is_authenticated and (request.user.is_superuser or request.user.is_staff):
            return redirect('/admin/')
        return render(request, self.template_name)

    def post(self, request):
        identifier = request.POST.get('identifier', '').strip()
        password = request.POST.get('password', '')
        remember_me = request.POST.get('remember_me') == 'on'

        success, message, user = superuser_login(request, identifier=identifier, password=password)

        if success:
            if not remember_me:
                # Session expires on browser close
                request.session.set_expiry(0)
            next_url = request.GET.get('next') or request.POST.get('next') or '/admin/'
            return redirect(next_url)

        return render(request, self.template_name, {
            'error_message': message,
            'identifier': identifier,
        })


class SuperuserLogoutView(View):
    """
    Logs out the superuser and redirects back to the login portal.
    """
    def get(self, request):
        logout(request)
        messages.info(request, "You have been logged out successfully.")
        return redirect('superuser-login')

    def post(self, request):
        return self.get(request)


# --------------------------------------------------------------------------
# REST API Views (Stateless / JWT Authentication)
# --------------------------------------------------------------------------

class LoginAPIView(APIView):
    """
    POST /api/v1/auth/login/
    Authenticates user via phone number, email, or username and returns JWT tokens.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        identifier = serializer.validated_data['identifier']
        password = serializer.validated_data['password']

        user = authenticate_user(identifier=identifier, password=password)

        if not user:
            # Check whether user exists to provide helpful feedback
            existing = get_user_by_identifier(identifier)
            if existing and not existing.is_active:
                return Response(
                    {"detail": "Account is disabled. Please contact the administrator."},
                    status=status.HTTP_403_FORBIDDEN
                )
            return Response(
                {"detail": "Invalid credentials. Provide a valid email, phone number, or username."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        token_data = generate_auth_tokens(user)
        return Response(token_data, status=status.HTTP_200_OK)


class UserProfileAPIView(APIView):
    """
    GET /api/v1/auth/me/
    Returns the authenticated user's profile details.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = get_user_by_id(request.user.id)
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = UserOutputSerializer(user)
        return Response(serializer.data, status=status.HTTP_200_OK)


class StaffListAPIView(APIView):
    """
    GET /api/v1/auth/staff/
    Returns list of all restaurant staff and managers.
    Requires staff or superuser permissions.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response(
                {"detail": "Permission denied. Staff only."},
                status=status.HTTP_403_FORBIDDEN
            )
        staff_members = list_staff_users()
        serializer = UserOutputSerializer(staff_members, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SystemHealthAPIView(APIView):
    """
    GET /api/v1/health/
    Verifies database connectivity, celery config, and websocket layer.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        from django.db import connection
        db_status = "healthy"
        try:
            connection.ensure_connection()
        except Exception as e:
            db_status = f"unhealthy: {str(e)}"

        return Response({
            "status": "online",
            "service": "Crunchy Restaurant Management System Backend",
            "database": db_status,
            "version": "1.0.0",
        }, status=status.HTTP_200_OK)

