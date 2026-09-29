from .models import CustomerCart, CustomerAddress


def cart_data(cart):
    return {'items': cart.items, 'version': cart.version}


def get_cart(user, branch):
    cart = CustomerCart.objects.filter(user=user, branch=branch).first()
    return cart_data(cart) if cart else {'items': [], 'version': 0}


def addresses(user):
    return CustomerAddress.objects.filter(user=user)
