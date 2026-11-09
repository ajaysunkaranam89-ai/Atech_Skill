"""Small generic CRUD layer used by every management screen.

Gives each screen search, filtering, sorting, pagination, audit logging and
role checks without repeating boilerplate.
"""
from django.contrib import messages
from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django.views import generic

from apps.core import audit

# Capabilities -> who has them
CAPABILITIES = {
    "staff": lambda u: u.is_academy_staff,  # super admin, course admin, trainer
    "content": lambda u: u.can_manage_all_content,  # super admin, course admin
    "super": lambda u: u.is_super_admin,
}


def has_cap(user, cap):
    return user.is_authenticated and user.is_active and CAPABILITIES[cap](user)


class StaffRequiredMixin(AccessMixin):
    capability = "staff"
    section = ""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not has_cap(request.user, self.capability):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["section"] = self.section
        return ctx


def badge(value, label=None):
    colors = {
        "published": "success", "active": "success", "paid": "success", "approved": "success", "completed": "secondary",
        "ongoing": "info", "graded": "success", "scheduled": "info",
        "draft": "secondary", "pending": "warning", "created": "warning", "upcoming": "info", "submitted": "warning",
        "unpublished": "dark", "archived": "dark", "expired": "dark", "cancelled": "danger", "failed": "danger",
        "rejected": "danger", "refunded": "dark", "resubmit": "warning", True: "success", False: "secondary",
    }
    if isinstance(value, bool):
        label = label or ("Yes" if value else "No")
    return format_html('<span class="badge text-bg-{} status-badge">{}</span>', colors.get(value, "light"), label or value)


class ManageListView(StaffRequiredMixin, generic.ListView):
    template_name = "dashboard/list.html"
    paginate_by = 20
    title = ""
    search_fields = []
    filters = []  # [(param, label, [(value,label),...]) or callable(view)->choices]
    sort_options = {}  # {key: (label, ordering)}
    default_sort = None
    create_url = None
    create_label = "Add new"
    edit_url = None
    delete_url = None
    extra_buttons = []  # [(label, url, icon)]

    def base_queryset(self):
        return self.model.objects.all()

    def get_queryset(self):
        qs = self.base_queryset()
        q = self.request.GET.get("q", "").strip()
        if q and self.search_fields:
            cond = Q()
            for f in self.search_fields:
                cond |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(cond)
        for param, _label, _choices in self.get_filters():
            val = self.request.GET.get(param)
            if val not in (None, ""):
                qs = self.apply_filter(qs, param, val)
        sort = self.request.GET.get("sort") or self.default_sort
        if sort in self.sort_options:
            ordering = self.sort_options[sort][1]
            qs = qs.order_by(*(ordering if isinstance(ordering, (list, tuple)) else [ordering]))
        return qs.distinct() if self.search_fields and q else qs

    def apply_filter(self, qs, param, val):
        if val in ("true", "false"):
            val = val == "true"
        try:
            return qs.filter(**{param: val})
        except (ValueError, ValidationError):
            return qs.none()

    def get_filters(self):
        out = []
        for f in self.filters:
            param, label, choices = f
            out.append((param, label, choices(self) if callable(choices) else choices))
        return out

    def columns(self):
        """Return list of column headers."""
        return []

    def row(self, obj):
        """Return list of cell values (strings/HTML) matching columns()."""
        return []

    def actions(self, obj):
        acts = []
        if self.edit_url:
            acts.append({"label": "Edit", "url": reverse(self.edit_url, args=[obj.pk]), "icon": "pencil"})
        if self.delete_url and self.can_delete(obj):
            acts.append({"label": "Delete", "url": reverse(self.delete_url, args=[obj.pk]), "icon": "trash", "danger": True})
        return acts

    def can_delete(self, obj):
        return True

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx.update(
            title=self.title,
            headers=self.columns(),
            rows=[(o, self.row(o), self.actions(o)) for o in ctx["object_list"]],
            filters=[(p, label, choices, self.request.GET.get(p, "")) for p, label, choices in self.get_filters()],
            sort_options=[(k, v[0]) for k, v in self.sort_options.items()],
            current_sort=self.request.GET.get("sort") or self.default_sort or "",
            q=self.request.GET.get("q", ""),
            querystring=params.urlencode(),
            create_url=reverse(self.create_url) if self.create_url else None,
            create_label=self.create_label,
            extra_buttons=self.extra_buttons,
            has_search=bool(self.search_fields),
            total=ctx["paginator"].count if ctx.get("paginator") else len(ctx["object_list"]),
        )
        return ctx


class ActorFormMixin:
    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["actor"] = self.request.user
        return kw


class ManageFormMixin(StaffRequiredMixin, ActorFormMixin):
    template_name = "dashboard/form.html"
    success_url_name = None
    title = ""
    cancel_url_name = None
    upload_progress = False

    def get_success_url(self):
        return reverse(self.success_url_name)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = self.get_title()
        ctx["cancel_url"] = reverse(self.cancel_url_name or self.success_url_name)
        ctx["upload_progress"] = self.upload_progress
        ctx["fieldsets"] = getattr(ctx["form"], "fieldsets", None)
        return ctx

    def get_title(self):
        return self.title

    def get_initial(self):
        init = super().get_initial()
        fields = self.get_form_class().base_fields
        for k, v in self.request.GET.items():
            if k in fields and k not in init:
                init[k] = v
        return init

    def is_ajax(self):
        return self.request.headers.get("x-requested-with") == "XMLHttpRequest"

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.is_ajax():
            return JsonResponse({"ok": True, "redirect": response.url})
        return response


class ManageCreateView(ManageFormMixin, generic.CreateView):
    def form_valid(self, form):
        if hasattr(form.instance, "created_by_id") and not form.instance.created_by_id:
            form.instance.created_by = self.request.user
        response = super().form_valid(form)
        audit.log(self.request, "create", self.object, audit.diff_form(form))
        messages.success(self.request, f"{self.model._meta.verbose_name.capitalize()} “{self.object}” created.")
        return response

    def get_title(self):
        return self.title or f"Add {self.model._meta.verbose_name}"


class ManageUpdateView(ManageFormMixin, generic.UpdateView):
    def form_valid(self, form):
        changes = audit.diff_form(form)
        response = super().form_valid(form)
        if changes:
            audit.log(self.request, "update", self.object, changes)
        messages.success(self.request, f"{self.model._meta.verbose_name.capitalize()} “{self.object}” saved.")
        return response

    def get_title(self):
        return self.title or f"Edit {self.model._meta.verbose_name}: {self.object}"


class ManageDeleteView(StaffRequiredMixin, generic.DeleteView):
    """Deletion always needs POST + (optionally) typing the object's name."""

    template_name = "dashboard/confirm_delete.html"
    success_url_name = None
    require_typed_confirmation = False
    warning = ""

    def confirmation_text(self):
        return str(self.object)

    def impact(self):
        return []

    def get_success_url(self):
        return reverse(self.success_url_name)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            title=f"Delete {self.model._meta.verbose_name}",
            require_typed=self.require_typed_confirmation,
            confirm_text=self.confirmation_text(),
            impact=self.impact(),
            warning=self.warning,
            cancel_url=self.get_success_url(),
        )
        return ctx

    def form_valid(self, form):
        if self.require_typed_confirmation:
            typed = self.request.POST.get("confirm_text", "").strip()
            if typed != self.confirmation_text().strip():
                messages.error(self.request, "Confirmation text did not match. Nothing was deleted.")
                return redirect(self.request.path)
        obj_repr = str(self.object)
        snap = audit.snapshot(self.object)
        audit.log(self.request, "delete", self.object, {"deleted": snap}, object_repr=obj_repr)
        response = super().form_valid(form)
        messages.success(self.request, f"Deleted “{obj_repr}”.")
        return response
