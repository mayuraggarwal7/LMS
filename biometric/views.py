import json
from datetime import date

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from courses.access import course_teacher
from hr.models import FacultyProfile
from institution.views import admin_required

from .models import Device, Punch
from .services import course_correlation, ingest, ingest_csv, staff_attendance


@admin_required
def overview(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "device":
            name = (request.POST.get("name") or "").strip()
            if name:
                d = Device.objects.create(name=name, location=request.POST.get("location", ""), vendor=request.POST.get("vendor", ""),
                                          serial_no=request.POST.get("serial_no", ""))
                messages.success(request, f"Device added. API key: {d.api_key}")
        elif action == "upload" and request.FILES.get("file"):
            device = Device.objects.filter(pk=request.POST.get("device")).first()
            text = request.FILES["file"].read().decode("utf-8-sig", errors="replace")
            r = ingest_csv(text, device)
            messages.success(request, f"Imported {r['created']} punches ({r['duplicates']} duplicates, {r['errors']} unreadable rows).")
            if r["unknown"]:
                messages.warning(request, f"{r['unknown']} punches have ids not linked to anyone: {', '.join(r['unknown_ids'])}")
        return redirect("biometric")
    today = timezone.localdate()
    start = date.fromisoformat(request.GET["from"]) if request.GET.get("from") else today.replace(day=1)
    end = date.fromisoformat(request.GET["to"]) if request.GET.get("to") else today
    staff = [fp.user for fp in FacultyProfile.objects.filter(active=True).select_related("user")]
    rows = staff_attendance(staff, start, end)
    rows.sort(key=lambda r: (-len(r["classes_without_punch"]), r["days_present"]))
    today_punches = Punch.objects.filter(timestamp__date=today)
    return render(request, "biometric/overview.html", {
        "devices": Device.objects.all(), "rows": rows[:200], "start": start, "end": end,
        "today_people": today_punches.values("person").distinct().count(), "today_punches": today_punches.count(),
        "unlinked": Punch.objects.filter(person__isnull=True).count(),
        "recent": Punch.objects.select_related("person", "device").order_by("-timestamp")[:15],
    })


@course_teacher
def course_report(request, course):
    report = course_correlation(course)
    return render(request, "biometric/course_report.html", {"course": course, "tab": "attendance", "report": report})


@csrf_exempt
@require_POST
def api_punches(request):
    """POST /api/biometric/punches  header X-Device-Key: <key>
    body: {"punches":[{"biometric_id":"COMP001","timestamp":"2026-10-16T08:55:00+05:30","direction":"in"}]}"""
    key = request.headers.get("X-Device-Key") or request.GET.get("key")
    device = Device.objects.filter(api_key=key, active=True).first() if key else None
    if device is None:
        return JsonResponse({"error": "unknown or inactive device key"}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "body must be JSON"}, status=400)
    rows = payload.get("punches") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return JsonResponse({"error": "expected a list of punches"}, status=400)
    return JsonResponse(ingest(rows[:5000], device=device, source="api"))


@admin_required
@require_POST
def device_rotate(request, device_id):
    from .models import generate_api_key

    d = get_object_or_404(Device, pk=device_id)
    d.api_key = generate_api_key()
    d.save(update_fields=["api_key"])
    messages.success(request, f"New key for {d.name}: {d.api_key}")
    return redirect("biometric")
