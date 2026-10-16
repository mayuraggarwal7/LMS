from django.contrib import admin

from .models import Department, Institution, Program, Regulation, Rule


class RuleInline(admin.TabularInline):
    model = Rule
    extra = 1


@admin.register(Regulation)
class RegulationAdmin(admin.ModelAdmin):
    list_display = ["name", "scope", "effective_from", "active"]
    list_filter = ["scope", "active"]
    inlines = [RuleInline]


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "level", "intake", "nba_accredited", "department", "active"]
    list_filter = ["level", "nba_accredited", "active"]


admin.site.register([Institution, Department])
