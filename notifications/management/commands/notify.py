"""Refresh everyone's notification centre; optionally email each user a digest of new items.

    python manage.py notify            # refresh all active users (run hourly from cron)
    python manage.py notify --email    # ... and email new 'due soon' / 'overdue' items
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification
from notifications.services import refresh


class Command(BaseCommand):
    help = "Refresh notification centres and optionally email digests"

    def add_arguments(self, parser):
        parser.add_argument("--email", action="store_true", help="Email users their new due-soon/overdue items")

    def handle(self, *args, **opts):
        users = User.objects.filter(is_active=True)
        created = sent = 0
        for user in users.iterator():
            created += refresh(user)
            if opts["email"] and user.email:
                sent += self._email(user)
        self.stdout.write(self.style.SUCCESS(f"Refreshed {users.count()} users: {created} new notifications, {sent} emails sent."))

    def _email(self, user):
        items = list(Notification.objects.filter(user=user, resolved_at__isnull=True, actionable=True, emailed_at__isnull=True,
                                                 level__in=[Notification.WARN, Notification.URGENT]).order_by("level"))
        if not items:
            return 0
        site = getattr(settings, "LMS_SITE_URL", "").rstrip("/")
        lines = [f"Hello {user.display_name},", "", "These items need your attention in CampusFlow:", ""]
        for n in items:
            tag = "OVERDUE" if n.level == Notification.URGENT else "Due soon"
            lines.append(f"- [{tag}] {n.title}" + (f" - {n.body}" if n.body else ""))
            if site and n.url:
                lines.append(f"  {site}{n.url}")
        lines += ["", f"Notification centre: {site}/notifications/" if site else ""]
        send_mail(f"CampusFlow: {len(items)} item{'s' if len(items) != 1 else ''} need your attention", "\n".join(lines),
                  settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)
        Notification.objects.filter(pk__in=[n.pk for n in items]).update(emailed_at=timezone.now())
        return 1
