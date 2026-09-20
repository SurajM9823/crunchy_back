import re


def normalize_phone_number(phone: str) -> str:
    """
    Strips spaces, dashes, parentheses and keeps only + and digits.
    Example: '+1 (555) 019-2831' -> '+15550192831'
    """
    if not phone:
        return ''
    cleaned = re.sub(r'[^\d+]', '', phone.strip())
    return cleaned


def detect_identifier_type(identifier: str) -> str:
    """
    Determines whether the given login identifier is an email, phone number, or username.
    Returns: 'email', 'phone', or 'username'.
    """
    if not identifier:
        return 'username'
    identifier = identifier.strip()
    if '@' in identifier:
        return 'email'
    # If it contains only digits, +, -, (, ), spaces, and has at least 7 digits, it's a phone
    digits_only = re.sub(r'\D', '', identifier)
    if len(digits_only) >= 7 and re.match(r'^[\d+\-\(\)\s]+$', identifier):
        return 'phone'
    return 'username'

