from django.contrib import admin

from .models import AssessmentComponent, Exam, ExamAbsence, ExamMark, ExamQuestion, Question


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ["text", "course", "unit", "outcome", "marks", "bloom_level", "source"]
    list_filter = ["course", "bloom_level", "marks", "source"]


class ExamQuestionInline(admin.TabularInline):
    model = ExamQuestion
    extra = 0


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ["title", "course", "component", "date", "total_marks", "status"]
    inlines = [ExamQuestionInline]


admin.site.register([AssessmentComponent, ExamMark, ExamAbsence])
