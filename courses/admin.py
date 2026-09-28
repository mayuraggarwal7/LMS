from django.contrib import admin

from .models import Course, CourseOutcome, Enrollment, Experiment, PlanItem, Reference, Rubric, RubricCriterion, RubricLevel, Topic, Unit


class OutcomeInline(admin.TabularInline):
    model = CourseOutcome
    extra = 0


class UnitInline(admin.TabularInline):
    model = Unit
    extra = 0
    exclude = ["outcomes"]


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ["code", "title", "teacher", "year", "semester", "department", "join_code", "archived"]
    list_filter = ["year", "semester", "department", "archived"]
    search_fields = ["code", "title"]
    inlines = [OutcomeInline, UnitInline]


class TopicInline(admin.TabularInline):
    model = Topic
    extra = 0


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["course", "number", "title", "hours"]
    inlines = [TopicInline]


class LevelInline(admin.TabularInline):
    model = RubricLevel
    extra = 0


@admin.register(RubricCriterion)
class CriterionAdmin(admin.ModelAdmin):
    list_display = ["rubric", "title", "max_points", "outcome"]
    inlines = [LevelInline]


admin.site.register([Enrollment, PlanItem, Reference, Experiment, Rubric])
