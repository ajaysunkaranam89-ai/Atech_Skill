from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def inbox(request):
    page = Paginator(Notification.objects.filter(user=request.user), 25).get_page(request.GET.get("page"))
    return render(request, "notifications/inbox.html", {"page": page})


@login_required
@require_POST
def open_notification(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    if not n.is_read:
        n.is_read = True
        n.save(update_fields=["is_read"])
    if n.link.startswith("/"):
        return redirect(n.link)
    return redirect("notifications:inbox")


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect("notifications:inbox")
