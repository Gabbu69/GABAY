from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from prices.permissions import ENCODER_ROLE, RESEARCH_ADMIN_ROLE, VERIFIER_ROLE


class Command(BaseCommand):
    help = "Create the GABAY maker-checker role groups without creating user accounts."

    def handle(self, *args, **options):
        created = []
        for name in (ENCODER_ROLE, VERIFIER_ROLE, RESEARCH_ADMIN_ROLE):
            _, was_created = Group.objects.get_or_create(name=name)
            if was_created:
                created.append(name)
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created groups: {', '.join(created)}"))
        else:
            self.stdout.write("GABAY role groups already exist.")
