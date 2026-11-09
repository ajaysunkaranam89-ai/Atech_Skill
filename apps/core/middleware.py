class SecurityHeadersMiddleware:
    """Extra headers not covered by Django's SecurityMiddleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(self \"https://checkout.razorpay.com\" \"https://api.razorpay.com\")")
        response.setdefault("Cross-Origin-Opener-Policy", "same-origin-allow-popups")
        if request.path.startswith(("/dashboard/", "/learn/", "/accounts/")):
            response.setdefault("Cache-Control", "no-store, private")
        return response


class LeadAttributionMiddleware:
    """Remember the first traffic source of a visitor (UTM tags / external referrer) in the session,
    so an enquiry submitted later can be attributed to the campaign or search engine that brought them."""

    KEYS = ("utm_source", "utm_medium", "utm_campaign")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "GET" and not request.path.startswith(("/static/", "/media/", "/dashboard/", "/api/")):
            session = getattr(request, "session", None)
            if session is not None and "lead_src" not in session:
                utm = {k: request.GET.get(k, "")[:100] for k in self.KEYS if request.GET.get(k)}
                ref = request.META.get("HTTP_REFERER", "")[:300]
                host = request.get_host()
                external_ref = ref if ref and host not in ref else ""
                if utm or external_ref:
                    session["lead_src"] = {**utm, "referrer": external_ref, "landing": request.path[:300]}
        return self.get_response(request)
