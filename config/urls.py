from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from accounts.views import SignUpView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("auth/signup/", SignUpView.as_view(), name="signup"),
    path("auth/", include("django.contrib.auth.urls")),
    path("", include("finance.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
