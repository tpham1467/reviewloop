from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from .models import Note


@login_required
def list_notes(request):
    rows = Note.objects.filter(owner=request.user)
    return JsonResponse({"notes": [n.title for n in rows]})
