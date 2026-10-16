"""Fee invoicing, payments, payroll and the institution ledger."""

import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Max

from biometric.services import days_present
from delivery.models import ClassSession
from hr.models import FacultyProfile
from students.models import StudentProfile

from .models import Concession, FeeStructure, Invoice, Payment, PayrollRun, Payslip, SalaryStructure

TWO = Decimal("0.01")


def money(v):
    return Decimal(v).quantize(TWO, rounding=ROUND_HALF_UP)


def structure_for(student, academic_year, year_of_study=None):
    year = year_of_study or student.year_of_study
    qs = FeeStructure.objects.filter(program=student.program, academic_year=academic_year, year_of_study=year)
    return qs.filter(category=student.category).first() or qs.filter(category="").first()


def concession_amount(student, structure):
    total = Decimal("0")
    lines = {line.head_id: line.amount for line in structure.lines.all()}
    if student.admission_type == StudentProfile.TFWS:
        total += sum((line.amount for line in structure.lines.select_related("head") if line.head.waivable_under_tfws), Decimal("0"))
    for c in Concession.objects.filter(student=student, academic_year=structure.academic_year):
        base = lines.get(c.head_id, Decimal("0")) if c.head_id else structure.total
        total += base * c.percent / 100 + c.amount
    return min(money(total), structure.total)


@transaction.atomic
def raise_invoice(student, academic_year, due_date=None, year_of_study=None):
    structure = structure_for(student, academic_year, year_of_study)
    if structure is None:
        return None
    gross = structure.total
    concession = concession_amount(student, structure)
    invoice, _ = Invoice.objects.update_or_create(
        student=student, academic_year=academic_year,
        defaults={"year_of_study": structure.year_of_study, "structure": structure, "gross": gross,
                  "concession": concession, "amount": gross - concession, "due_date": due_date},
    )
    return invoice


def raise_invoices(academic_year, students=None, due_date=None):
    students = students if students is not None else StudentProfile.objects.filter(status=StudentProfile.ACTIVE)
    made = [inv for inv in (raise_invoice(s, academic_year, due_date) for s in students) if inv]
    return made


def next_receipt_no(on):
    prefix = f"R{on:%Y%m}-"
    last = Payment.objects.filter(receipt_no__startswith=prefix).aggregate(m=Max("receipt_no"))["m"]
    n = int(last.split("-")[1]) + 1 if last else 1
    return f"{prefix}{n:05d}"


def record_payment(invoice, amount, paid_on=None, mode="upi", reference="", user=None):
    paid_on = paid_on or date.today()
    amount = money(amount)
    if amount <= 0:
        raise ValueError("Amount must be positive")
    if amount > invoice.balance:
        raise ValueError(f"Amount exceeds the balance of {invoice.balance}")
    return Payment.objects.create(invoice=invoice, amount=amount, paid_on=paid_on, mode=mode, reference=reference,
                                  receipt_no=next_receipt_no(paid_on), recorded_by=user)


def student_ledger(student):
    rows, balance = [], Decimal("0")
    for inv in student.invoices.prefetch_related("payments").order_by("academic_year"):
        balance += inv.amount
        rows.append({"date": inv.created_at.date(), "text": f"Fees {inv.academic_year} (year {inv.year_of_study})",
                     "debit": inv.amount, "credit": None, "balance": balance, "invoice": inv})
        for p in sorted(inv.payments.all(), key=lambda p: p.paid_on):
            balance -= p.amount
            rows.append({"date": p.paid_on, "text": f"Payment {p.receipt_no} ({p.get_mode_display()})",
                         "debit": None, "credit": p.amount, "balance": balance, "payment": p})
    return {"rows": rows, "balance": balance}


# --- payroll -----------------------------------------------------------------------------

def salary_for(faculty, on):
    return faculty.salary_structures.filter(effective_from__lte=on).order_by("-effective_from").first()


def compute_payslip(run, faculty, leave_days=0):
    start = run.month.replace(day=1)
    end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    salary = salary_for(faculty, end)
    present = days_present(faculty.user, start, end)
    breakdown = {}
    if faculty.employment == FacultyProfile.VISITING:
        hours = Decimal(ClassSession.objects.filter(course__teacher=faculty.user, status=ClassSession.COMPLETED,
                                                    date__gte=start, date__lte=end).count())
        rate = salary.hourly_rate if salary else Decimal("0")
        gross = money(hours * rate)
        breakdown = {"hours": str(hours), "rate": str(rate)}
        deductions = money(salary.tds if salary else 0)
        lop = 0
    else:
        hours = Decimal("0")
        if salary is None:
            gross = deductions = Decimal("0")
            lop = 0
        else:
            lop = max(0, run.working_days - present - leave_days)
            full = salary.gross
            per_day = full / run.working_days if run.working_days else Decimal("0")
            gross = money(full - per_day * lop)
            basic_da = salary.basic * (1 + salary.da_percent / 100)
            pf = money(basic_da * salary.pf_percent / 100)
            deductions = money(pf + salary.professional_tax + salary.tds)
            breakdown = {"basic": str(salary.basic), "da": str(money(salary.basic * salary.da_percent / 100)),
                         "hra": str(money(salary.basic * salary.hra_percent / 100)), "other": str(salary.other_allowances),
                         "lop_deduction": str(money(per_day * lop)), "pf": str(pf), "pt": str(salary.professional_tax),
                         "tds": str(salary.tds)}
    slip, _ = Payslip.objects.update_or_create(
        run=run, faculty=faculty,
        defaults={"days_present": present, "leave_days": leave_days, "lop_days": lop, "teaching_hours": hours,
                  "gross": gross, "deductions": deductions, "net": gross - deductions, "breakdown": breakdown},
    )
    return slip


@transaction.atomic
def run_payroll(month, working_days=26, leaves=None):
    run, _ = PayrollRun.objects.get_or_create(month=month.replace(day=1), defaults={"working_days": working_days})
    if run.status != PayrollRun.DRAFT:
        raise ValueError("This payroll is already approved")
    run.working_days = working_days
    run.save(update_fields=["working_days"])
    leaves = leaves or {}
    staff = FacultyProfile.objects.filter(active=True).select_related("user")
    for f in staff:
        compute_payslip(run, f, leaves.get(f.pk, 0))
    return run


def institution_ledger(academic_year=None):
    invoices = Invoice.objects.all()
    if academic_year:
        invoices = invoices.filter(academic_year=academic_year)
    billed = sum((i.amount for i in invoices), Decimal("0"))
    concessions = sum((i.concession for i in invoices), Decimal("0"))
    collected = sum((p.amount for p in Payment.objects.filter(invoice__in=invoices)), Decimal("0"))
    payroll = sum((s.net for s in Payslip.objects.filter(run__status__in=[PayrollRun.APPROVED, PayrollRun.PAID])), Decimal("0"))
    return {"billed": billed, "concessions": concessions, "collected": collected, "outstanding": billed - collected,
            "payroll_paid": payroll, "invoices": invoices.count(),
            "defaulters": sum(1 for i in invoices.prefetch_related("payments") if i.balance > 0),
            "salary_structures": SalaryStructure.objects.count()}
