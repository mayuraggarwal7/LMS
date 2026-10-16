from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from institution.views import admin_required, get_institution
from students.models import StudentProfile

from .models import FeeStructure, Invoice, Payment, PayrollRun
from .services import institution_ledger, raise_invoices, record_payment, run_payroll, student_ledger


def current_academic_year():
    today = timezone.localdate()
    start = today.year if today.month >= 6 else today.year - 1
    return f"{start}-{str(start + 1)[2:]}"


@admin_required
def dashboard(request):
    inst = get_institution()
    ay = request.GET.get("ay") or current_academic_year()
    if request.method == "POST" and request.POST.get("action") == "raise":
        made = raise_invoices(ay, StudentProfile.objects.filter(program__institution=inst, status=StudentProfile.ACTIVE))
        messages.success(request, f"{len(made)} invoice(s) raised or refreshed for {ay}.")
        return redirect(f"{request.path}?ay={ay}")
    invoices = Invoice.objects.filter(academic_year=ay, student__program__institution=inst).select_related(
        "student__user", "student__program").prefetch_related("payments")
    by_program = {}
    for inv in invoices:
        row = by_program.setdefault(inv.student.program, {"billed": Decimal("0"), "collected": Decimal("0"), "count": 0, "defaulters": 0})
        row["billed"] += inv.amount
        row["collected"] += inv.paid
        row["count"] += 1
        row["defaulters"] += int(inv.balance > 0)
    defaulters = sorted((i for i in invoices if i.balance > 0), key=lambda i: -i.balance)[:25]
    years = sorted(set(Invoice.objects.values_list("academic_year", flat=True)) | {ay}, reverse=True)
    return render(request, "finance/dashboard.html", {
        "ay": ay, "years": years, "ledger": institution_ledger(ay), "by_program": by_program, "defaulters": defaulters,
        "recent": Payment.objects.select_related("invoice__student__user").order_by("-paid_on", "-pk")[:10],
        "structures": FeeStructure.objects.filter(program__institution=inst, academic_year=ay).select_related("program").prefetch_related("lines__head"),
        "payroll": PayrollRun.objects.all()[:6],
    })


@admin_required
def student_account(request, student_id):
    sp = get_object_or_404(StudentProfile.objects.select_related("user", "program"), pk=student_id)
    if request.method == "POST":
        inv = get_object_or_404(Invoice, pk=request.POST.get("invoice"), student=sp)
        try:
            p = record_payment(inv, Decimal(request.POST.get("amount") or "0"),
                               date.fromisoformat(request.POST.get("paid_on")) if request.POST.get("paid_on") else None,
                               request.POST.get("mode") or "upi", request.POST.get("reference", ""), request.user)
            messages.success(request, f"Receipt {p.receipt_no}: ₹{p.amount} recorded.")
        except (ValueError, InvalidOperation) as e:
            messages.error(request, str(e))
        return redirect("student_account", sp.pk)
    return render(request, "finance/student_account.html", {
        "sp": sp, "ledger": student_ledger(sp), "invoices": sp.invoices.prefetch_related("payments"),
        "modes": Payment.MODES, "concessions": sp.concessions.select_related("head"), "today": timezone.localdate(),
    })


@admin_required
def payroll(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "run":
            month = date.fromisoformat(request.POST.get("month") + "-01")
            try:
                run = run_payroll(month, int(request.POST.get("working_days") or 26))
                messages.success(request, f"{run} computed for {run.payslips.count()} staff.")
                return redirect("payroll_run", run.pk)
            except ValueError as e:
                messages.error(request, str(e))
        return redirect("payroll")
    today = timezone.localdate()
    return render(request, "finance/payroll.html", {"runs": PayrollRun.objects.prefetch_related("payslips"),
                                                    "default_month": f"{today:%Y-%m}"})


@admin_required
def payroll_run(request, run_id):
    run = get_object_or_404(PayrollRun, pk=run_id)
    if request.method == "POST":
        new = request.POST.get("status")
        if new in dict(PayrollRun.STATUSES):
            run.status = new
            run.save(update_fields=["status"])
            messages.success(request, f"Payroll marked {run.get_status_display().lower()}.")
        return redirect("payroll_run", run.pk)
    slips = run.payslips.select_related("faculty__user", "faculty__department")
    return render(request, "finance/payroll_run.html", {"run": run, "slips": slips,
                                                        "totals": {"gross": sum(s.gross for s in slips), "net": sum(s.net for s in slips),
                                                                   "lop": sum(s.lop_days for s in slips)}})
