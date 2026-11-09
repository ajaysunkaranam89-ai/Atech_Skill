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
