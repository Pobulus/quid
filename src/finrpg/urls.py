from django.urls import path

from game.api import api
from game.views import index

urlpatterns = [
    path("", index, name="index"),
    path("api/", api.urls),
]
