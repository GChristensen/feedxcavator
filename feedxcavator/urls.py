"""URL routing (API2-1, API2-2)."""

from django.urls import path, re_path

from feedxcavator import views
from feedxcavator.api import api

urlpatterns = [
    path("", views.home),
    path("console", views.console),
    path("login", views.login),
    path("logout", views.logout),
    path("favicon.ico", views.favicon),
    re_path(r"^static/(?P<path>.+)$", views.static),
    re_path(r"^feed/(?P<suffix>.+)$", views.feed),
    re_path(r"^image/(?P<blob_id>[^/]+)$", views.image),
    path("api/images/save", views.save_image),
    re_path(r"^api/wordfilter/(?P<action>[\w-]+)$", views.wordfilter),
    re_path(r"^handler/(?P<name>[^/]+)/?$", views.handler),
    re_path(r"^redirect/(?P<random>[^/]*)/(?P<url>.+)$", views.redirect),
    re_path(r"^redirect-b64/(?P<random>[^/]*)/(?P<data>.+)$", views.redirect_b64),
    re_path(r"^forward/(?:(?P<random>[^/]*)/?)?$", views.forward),
    re_path(r"^forward-b64/(?P<data>[^/]+)$", views.forward_b64),
    path("websub", views.websub_hub),
    re_path(r"^backend/run/(?P<task>.+)$", views.run_task),
    path("backend/reschedule", views.reschedule),
    path("backend/service-task", views.service_task),
    path("front/api/", api.urls),
]

handler404 = views.not_found
