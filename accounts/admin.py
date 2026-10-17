from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class LMSUserAdmin(UserAdmin):
    list_display = ["username", "first_name", "last_name", "role", "department", "year_of_study", "roll_no"]
    list_filter = ["role", "department", "year_of_study"]
    fieldsets = UserAdmin.fieldsets + (("LMS profile", {"fields": ["role", "department", "year_of_study", "roll_no"]}),)
