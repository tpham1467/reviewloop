from datetime import timedelta

from django.utils import timezone

from .models import Note

TTL = timedelta(days=30)


def cutoff():
    return timezone.now() - TTL


def count():
    return Note.objects.count()
