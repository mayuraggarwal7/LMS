from django.contrib import admin

from .models import Concession, FeeHead, FeeLine, FeeStructure, Invoice, Payment, PayrollRun, Payslip, SalaryStructure


class FeeLineInline(admin.TabularInline):
    model = FeeLine
    extra = 1


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ["program", "academic_year", "year_of_study", "category", "approved_by"]
    list_filter = ["academic_year", "program"]
    inlines = [FeeLineInline]


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["student", "academic_year", "gross", "concession", "amount"]
    list_filter = ["academic_year"]
    inlines = [PaymentInline]


admin.site.register([FeeHead, Concession, Payment, SalaryStructure, PayrollRun, Payslip])
