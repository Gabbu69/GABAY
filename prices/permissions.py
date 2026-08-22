from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

ENCODER_ROLE = "encoder"
VERIFIER_ROLE = "verifier"
RESEARCH_ADMIN_ROLE = "research_admin"


def has_role(user, *roles: str) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=roles).exists()


def can_encode(user) -> bool:
    return has_role(user, ENCODER_ROLE, RESEARCH_ADMIN_ROLE)


def can_verify(user) -> bool:
    return has_role(user, VERIFIER_ROLE, RESEARCH_ADMIN_ROLE)


def can_administer_research(user) -> bool:
    return has_role(user, RESEARCH_ADMIN_ROLE)


def role_required(check: Callable):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not check(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


encoder_required = role_required(can_encode)
verifier_required = role_required(can_verify)
research_admin_required = role_required(can_administer_research)
