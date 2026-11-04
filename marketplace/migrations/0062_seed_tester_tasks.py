from django.db import migrations

# Default 14-day tester tutorial. Deliberately read-only/low-impact tasks so
# testers exercise the app every day without posting fake jobs, quotes or
# orders onto the live marketplace. Editable afterwards in the admin.
DEFAULT_TASKS = [
    (1,  'Install the app and sign in',
         'Install the Coconut Wireless app from the Google Play test link, sign in, and allow notifications when asked.', ''),
    (2,  'Check your notification settings',
         'Open your notification settings and choose which emails you want to receive.', '/notices/settings/'),
    (3,  'Browse local professionals',
         'Open the professionals directory and look through a few profiles.', '/professionals/'),
    (4,  'Search by town and trade',
         'In the professionals directory, filter by your town and a trade you might need.', '/professionals/'),
    (5,  'Look at open jobs',
         'Open the jobs board and look at a few jobs that have been posted.', '/tasks/'),
    (6,  'Explore the Market',
         'Open the Market and look at the items and services people are selling.', '/market/'),
    (7,  'Read your Notices',
         'Open your Notices (the bell icon) and check that your tester messages are showing up.', '/notices/'),
    (8,  'Read "How it works"',
         'Read the How it works page. Is anything confusing? Tell us in the feedback box.', '/how-it-works/'),
    (9,  'Open your messages',
         'Open your Messages inbox and check that it loads properly.', '/messages/'),
    (10, 'Visit your dashboard',
         'Open your dashboard and look at everything on it.', '/dashboard/'),
    (11, 'Use the app on mobile data',
         'Turn off Wi-Fi and use the app on mobile data for a few minutes. Does everything load?', ''),
    (12, 'Close and reopen the app',
         'Fully close the app, reopen it, and check that you are still signed in.', ''),
    (13, 'Check the Support page',
         'Open the Support page and check you can see how to contact us.', '/support/'),
    (14, 'Tell us what you think',
         'Last day! Use the app however you like, then tell us in the feedback box what you liked and what we should fix.', ''),
]


def seed(apps, schema_editor):
    TesterTask = apps.get_model('marketplace', 'TesterTask')
    for day, title, instructions, link_path in DEFAULT_TASKS:
        TesterTask.objects.get_or_create(
            day=day, defaults={'title': title, 'instructions': instructions, 'link_path': link_path},
        )


def unseed(apps, schema_editor):
    apps.get_model('marketplace', 'TesterTask').objects.filter(day__in=[d[0] for d in DEFAULT_TASKS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0061_app_tester_program'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
