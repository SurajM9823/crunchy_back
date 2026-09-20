# Crunchy Backend — Restaurant Management System (RMS)

A robust backend architecture for Restaurant Management built with **Django 5.1**, **Django REST Framework (DRF)**, **Django Channels (WebSockets)**, **Celery**, and **Redis**.

---

## Architecture Highlights

1. **Service-Selector Pattern**:
   - `models.py`: Database models (Custom `User` with roles like Superadmin, Owner, Manager, Chef, Waiter, Cashier).
   - `selectors.py`: Business logic for querying data (zero side-effects).
   - `services.py`: Business logic for data mutations, validations, and transactions.
   - `serializers.py`: DRF input validation and response shaping.
   - `views.py`: Thin controllers routing requests to services/selectors.
   - `backends.py`: Multi-Identifier Authentication Backend.
   - `consumers.py` & `routing.py`: WebSocket consumers for real-time kitchen, order, and table alerts.
   - `tasks.py`: Celery background asynchronous tasks.

2. **Multi-Identifier Authentication**:
   - Authenticate seamlessly using any of the following:
     - **📞 Phone Number** (e.g. `+1 (555) 019-2831` or `+15550192831`)
     - **✉️ Email Address** (e.g. `admin@crunchy.local`)
     - **👤 Username** (e.g. `admin`)
   - Fully active across Django Admin (`/admin/`), the Superuser Web Portal (`/superuser/login/`), and REST API (`/api/v1/auth/login/`).

3. **Superuser Web Portal**:
   - Modern, restaurant-branded login page at `/superuser/login/` with live identifier auto-detection, password visibility toggling, CSRF security, and session management.

4. **Background Workers & Real-Time**:
   - **Celery**: Initialized in `crunchy_backend/celery.py` with auto-discovery of tasks.
   - **Django Channels & Daphne**: ASGI configured in `crunchy_backend/asgi.py` with WebSocket routing.
   - **Redis**: Centralized cache, Celery broker, and Channel layer configured via `.env`.

---

## Quick Start

### 1. Environment & Dependencies
```powershell
# Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# (Already configured) Dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration (.env)
The project loads configurations from `.env` (a template is provided in `.env.example`):
- `DEBUG=True`
- `DATABASE_URL=sqlite:///db.sqlite3`
- `REDIS_URL=redis://127.0.0.1:6379/1`
- `CELERY_BROKER_URL=redis://127.0.0.1:6379/0`
- `CHANNEL_LAYER_TYPE=inmemory` (or `redis` when Redis is running)

### 3. Run Migrations & Initial Setup
```powershell
python manage.py migrate
python manage.py setup_initial_superuser
```

Default initial superuser credentials:
- **Username**: `admin`
- **Email**: `admin@crunchy.local`
- **Phone Number**: `+15550192831`
- **Password**: `Admin@123456`

### 4. Running the Development Server (with WebSockets)
Run with Daphne (ASGI):
```powershell
python manage.py runserver
```
- Superuser Login Portal: [http://127.0.0.1:8000/superuser/login/](http://127.0.0.1:8000/superuser/login/)
- Django Admin: [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)
- API Health Check: [http://127.0.0.1:8000/api/v1/health/](http://127.0.0.1:8000/api/v1/health/)
- Real-time Alert WebSocket: `ws://127.0.0.1:8000/ws/alerts/`

### 5. Running Celery Worker
```powershell
celery -A crunchy_backend worker -l info -P solo
```

---

## REST API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/auth/login/` | Multi-identifier login (returns JWT tokens) |
| `POST` | `/api/v1/auth/token/refresh/` | Refresh JWT access token |
| `GET` | `/api/v1/auth/me/` | Fetch current user profile |
| `GET` | `/api/v1/auth/staff/` | List all staff members (staff only) |
| `GET` | `/api/v1/health/` | System status & database connectivity |

