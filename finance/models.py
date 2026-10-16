"""Accounts: every student's fees and every staff member's payouts."""

from decimal import Decimal

from django.conf import settings
from django.db import models

from institution.models import Program


class FeeHead(models.Model):
    name = models.CharField(max_length=80, unique=True)
    refundable = models.BooleanField(default=False)
    waivable_under_tfws = models.BooleanField(default=False, help_text="Waived for Tuition Fee Waiver Scheme students")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class FeeStructure(models.Model):
    """Fee for one programme, year of study and academic year (optionally per admission category)."""

    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="fee_structures")
    academic_year = models.CharField(max_length=10, help_text="e.g. 2026-27")
    year_of_study = models.PositiveSmallIntegerField(default=1)
    category = models.CharField(max_length=10, blank=True, help_text="Blank = all categories")
    approved_by = models.CharField(max_length=120, blank=True, help_text="e.g. Fee Regulating Authority (FRA) order no.")

    class Meta:
        ordering = ["program__code", "academic_year", "year_of_study"]
        unique_together = [("program", "academic_year", "year_of_study", "category")]

    def __str__(self):
        cat = f" [{self.category}]" if self.category else ""
        return f"{self.program.code} Y{self.year_of_study} {self.academic_year}{cat}"

    @property
    def total(self):
        return sum((line.amount for line in self.lines.all()), Decimal("0"))


class FeeLine(models.Model):
    structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name="lines")
    head = models.ForeignKey(FeeHead, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["head__order"]


class Concession(models.Model):
    student = models.ForeignKey("students.StudentProfile", on_delete=models.CASCADE, related_name="concessions")
    academic_year = models.CharField(max_length=10)
    head = models.ForeignKey(FeeHead, on_delete=models.SET_NULL, null=True, blank=True, help_text="Blank = whole fee")
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reason = models.CharField(max_length=120, help_text="e.g. TFWS, Government scholarship (EBC), Merit")


class Invoice(models.Model):
    student = models.ForeignKey("students.StudentProfile", on_delete=models.CASCADE, related_name="invoices")
    academic_year = models.CharField(max_length=10)
    year_of_study = models.PositiveSmallIntegerField()
    structure = models.ForeignKey(FeeStructure, on_delete=models.SET_NULL, null=True, blank=True)
    gross = models.DecimalField(max_digits=10, decimal_places=2)
    concession = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount = models.DecimalField("Net payable", max_digits=10, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-academic_year", "student"]
        unique_together = [("student", "academic_year")]

    def __str__(self):
        return f"{self.student} {self.academic_year}: {self.amount}"

    @property
    def paid(self):
        return sum((p.amount for p in self.payments.all()), Decimal("0"))

    @property
    def balance(self):
        return self.amount - self.paid


class Payment(models.Model):
    MODES = [("upi", "UPI"), ("neft", "NEFT / RTGS"), ("card", "Card"), ("cheque", "Cheque / DD"), ("cash", "Cash"),
             ("scholarship", "Scholarship credit")]
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_on = models.DateField()
    mode = models.CharField(max_length=12, choices=MODES, default="upi")
    reference = models.CharField(max_length=80, blank=True)
    receipt_no = models.CharField(max_length=30, unique=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-paid_on", "-pk"]


class SalaryStructure(models.Model):
    faculty = models.ForeignKey("hr.FacultyProfile", on_delete=models.CASCADE, related_name="salary_structures")
    effective_from = models.DateField()
    basic = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    da_percent = models.DecimalField("DA %", max_digits=5, decimal_places=2, default=0)
    hra_percent = models.DecimalField("HRA %", max_digits=5, decimal_places=2, default=0)
    other_allowances = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pf_percent = models.DecimalField("PF % of basic+DA", max_digits=5, decimal_places=2, default=12)
    professional_tax = models.DecimalField(max_digits=8, decimal_places=2, default=200)
    tds = models.DecimalField("TDS per month", max_digits=10, decimal_places=2, default=0)
    hourly_rate = models.DecimalField("Rate per teaching hour (visiting)", max_digits=8, decimal_places=2, default=0)

    class Meta:
        ordering = ["faculty", "-effective_from"]

    @property
    def gross(self):
        da = self.basic * self.da_percent / 100
        hra = self.basic * self.hra_percent / 100
        return self.basic + da + hra + self.other_allowances


class PayrollRun(models.Model):
    DRAFT, APPROVED, PAID = "draft", "approved", "paid"
    STATUSES = [(DRAFT, "Draft"), (APPROVED, "Approved"), (PAID, "Paid")]
    month = models.DateField(help_text="First day of the month")
    working_days = models.PositiveSmallIntegerField(default=26)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month"]
        unique_together = [("month",)]

    def __str__(self):
        return f"Payroll {self.month:%b %Y}"

    @property
    def total_net(self):
        return sum((s.net for s in self.payslips.all()), Decimal("0"))


class Payslip(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    faculty = models.ForeignKey("hr.FacultyProfile", on_delete=models.CASCADE, related_name="payslips")
    days_present = models.PositiveSmallIntegerField(default=0)
    leave_days = models.PositiveSmallIntegerField(default=0, help_text="Approved leave (paid)")
    lop_days = models.PositiveSmallIntegerField("Loss-of-pay days", default=0)
    teaching_hours = models.DecimalField(max_digits=6, decimal_places=1, default=0, help_text="For visiting faculty")
    gross = models.DecimalField(max_digits=10, decimal_places=2)
    deductions = models.DecimalField(max_digits=10, decimal_places=2)
    net = models.DecimalField(max_digits=10, decimal_places=2)
    breakdown = models.JSONField(default=dict)

    class Meta:
        ordering = ["faculty"]
        unique_together = [("run", "faculty")]
