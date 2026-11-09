from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from .crud import has_cap


def staff_required(capability="staff"):
    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not has_cap(request.user, capability):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return deco
