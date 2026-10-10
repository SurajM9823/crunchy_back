"""IP is staff context only; a browser credential owns guest conversations."""
from ipaddress import ip_address, ip_network
from django.conf import settings


def client_ip(request):
    try:
        peer = ip_address(request.META.get('REMOTE_ADDR', ''))
        networks = [ip_network(c.strip()) for c in getattr(settings, 'CHAT_TRUSTED_PROXY_CIDRS', '127.0.0.1/32,::1/128').split(',') if c.strip()]
        if any(peer in network for network in networks):
            # Nginx overwrites X-Real-IP; never trust forwarding from arbitrary peers.
            forwarded = request.META.get('HTTP_X_REAL_IP')
            if forwarded:
                return str(ip_address(forwarded))
        return str(peer)
    except ValueError:
        return None
