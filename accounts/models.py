from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user model.

    Introduced from day one even while it is almost empty: swapping the user
    model after the first migration is painful and usually means recreating
    the database.
    """

    currency = models.CharField("currency", max_length=3, default="EUR")

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self):
        return self.get_full_name() or self.username
