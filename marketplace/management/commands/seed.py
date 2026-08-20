"""
Management command: python manage.py seed

Seeds the database with realistic Fijian sample data.
Run with --flush to wipe existing data first.
"""
import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone

from django.core.management import call_command

from marketplace.models import (
    Message, PublicReview, PrivateReview, Quote, Task, TradieProfile, User,
    TradeCategory, TaskPhoto, PlatformSettings, PlatformFee, Invoice, InvoiceLine, Sponsor,
    QuotingAppointment, QuotingAppointmentSlot,
)
from marketplace import workspaces
from marketplace.utils import create_platform_fee_for_task


PASSWORD = 'testpass123'


class Command(BaseCommand):
    help = 'Seed with sample Fijian users, tasks, quotes, messages and reviews.'

    def add_arguments(self, parser):
        parser.add_argument('--flush', action='store_true', help='Delete all existing data first.')

    def handle(self, *args, **options):
        if options['flush']:
            self.stdout.write('Flushing existing data…')
            PrivateReview.objects.all().delete()
            PublicReview.objects.all().delete()
            Message.objects.all().delete()
            Quote.objects.all().delete()
            InvoiceLine.objects.all().delete()
            Invoice.objects.all().delete()
            PlatformFee.objects.all().delete()
            Task.objects.all().delete()
            TradieProfile.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            TradeCategory.objects.all().delete()
            Sponsor.objects.all().delete()
            PlatformSettings.objects.all().delete()
            self.stdout.write(self.style.WARNING('Existing data deleted.'))

        # Create trade categories
        self.stdout.write('Creating trade categories…')
        self._create_categories()

        # Create platform settings
        self.stdout.write('Creating platform settings…')
        self._create_platform_settings()

        self.stdout.write('Creating users…')
        # ── Tradies ──────────────────────────────────────────────────────────
        sailosi = self._make_tradie(
            email='sailosi.tora@example.fj',
            first='Sailosi', last='Tora',
            town='Suva', mobile='+679 720 1234',
            biz='Tora Plumbing Suva',
            exp='7-15 years',
            bio=(
                'Licensed plumber with over 10 years experience in residential and '
                'commercial work across Suva and Nausori. I specialise in leak repairs, '
                'hot water systems, and bathroom renovations. Fully insured.'
            ),
            trades=['plumbing'],
            service_towns=['Suva', 'Nausori'],
        )
        mereani = self._make_tradie(
            email='mereani.v@example.fj',
            first='Mereani', last='Vakacegu',
            town='Nadi', mobile='+679 730 5678',
            biz='Clean Touch Fiji',
            exp='3-7 years',
            bio=(
                'Professional cleaner based in Nadi. I offer thorough home, office, '
                'and end-of-lease cleaning services. Reliable, detail-oriented, and '
                'always on time. Available 7 days a week.'
            ),
            trades=['cleaning'],
            service_towns=['Nadi', 'Lautoka'],
        )
        rajesh = self._make_tradie(
            email='rajesh.kumar@example.fj',
            first='Rajesh', last='Kumar',
            town='Lautoka', mobile='+679 710 9012',
            biz='Kumar Electrical & Air Con',
            tin='FJ-78432',
            exp='15+ years',
            bio=(
                'Licensed electrician and certified HVAC technician. 15+ years experience '
                'in Lautoka and the Western Division. I handle everything from switchboard '
                'upgrades to split-system installations. Registered with the Fiji Electrical '
                'Authority.'
            ),
            trades=['electrical', 'aircon'],
            service_towns=['Lautoka', 'Nadi'],
        )

        # ── Clients ──────────────────────────────────────────────────────────
        priya = self._make_client(
            email='priya.sharma@example.fj',
            first='Priya', last='Sharma',
            town='Suva', mobile='+679 770 3456',
        )
        adi = self._make_client(
            email='adi.litia@example.fj',
            first='Adi', last='Litia',
            town='Nadi', mobile='+679 760 7890',
        )
        john = self._make_client(
            email='john.whippy@example.fj',
            first='John', last='Whippy',
            town='Lautoka', mobile='+679 750 2345',
        )

        self.stdout.write('Creating tasks…')
        # Task 1 — open, 2 quotes (Priya / Suva / plumbing)
        t1 = Task.objects.create(
            client=priya, title='Fix leaking kitchen tap',
            category='plumbing',
            description=(
                'The mixer tap in my kitchen has been dripping constantly for a week. '
                'It is a standard single-lever mixer, about 5 years old. Needs a new '
                'washer or cartridge. Please let me know if you need access to the meter.'
            ),
            budget=80, town='Suva',
            preferred_date=datetime.date.today() + datetime.timedelta(days=3),
        )
        t1.categories.set(TradeCategory.objects.filter(slug='plumbing'))
        
        # Task 2 — assigned to Mereani (Adi / Nadi / cleaning)
        t2 = Task.objects.create(
            client=adi, title='Full house clean – 3-bedroom home',
            category='cleaning',
            description=(
                'Need a thorough clean of my 3-bedroom home in Nadi before a family '
                'event. Includes kitchen, 2 bathrooms, all rooms and windows. '
                'Happy to provide cleaning supplies or please include in quote.'
            ),
            budget=120, town='Nadi',
            preferred_date=datetime.date.today() + datetime.timedelta(days=2),
            status=Task.STATUS_ASSIGNED,
            assigned_tradie=mereani,
        )
        t2.categories.set(TradeCategory.objects.filter(slug='cleaning'))
        
        # Task 3 — completed, reviewed (John / Lautoka / electrical + air con)
        t3 = Task.objects.create(
            client=john, title='Switchboard upgrade & new AC unit – colonial house',
            category='electrical',
            description=(
                'Old fuse-box style switchboard needs replacing with a modern RCD '
                'circuit breaker panel. PLUS installation of a new split-system air con. '
                '3-bedroom colonial home, single phase power. '
                'Must be done to Fiji Electrical Authority standards.'
            ),
            budget=450, town='Lautoka',
            preferred_date=datetime.date.today() - datetime.timedelta(days=7),
            status=Task.STATUS_COMPLETED,
            assigned_tradie=rajesh,
            final_job_value=Decimal('420.00'),
            completed_at=timezone.now() - datetime.timedelta(days=5),
        )
        t3.categories.set(TradeCategory.objects.filter(slug__in=['electrical', 'aircon']))
        
        # Task 4 — open, no quotes (Priya / Suva / gardening)
        t4 = Task.objects.create(
            client=priya, title='Garden cleanup and lawn mowing',
            category='gardening',
            description=(
                'My garden needs a good cleanup — lawn mowing, whipper snipping edges, '
                'and clearing some overgrown shrubs. Approx 400 m² block in Suva. '
                'Can do while I am at work.'
            ),
            budget=60, town='Suva',
        )
        t4.categories.set(TradeCategory.objects.filter(slug='gardening'))

        self.stdout.write('Creating quotes…')
        # Regular quote (no platform fee included)
        q1 = Quote.objects.create(
            task=t1, tradie=sailosi, price=75,
            base_price=75,
            include_platform_fee=False,
            includes_materials=True,
            estimated_job_duration='1-2 hours',
            message=(
                'Bula Priya! I\'m a licensed plumber based in Suva. I can replace the '
                'cartridge in under an hour — price includes parts and labour. '
                'Available Tuesday or Wednesday morning.'
            ),
        )
        
        # Quote from another tradie
        q2 = Quote.objects.create(
            task=t1, tradie=rajesh, price=90,
            base_price=90,
            include_platform_fee=False,
            estimated_job_duration='1 hour',
            message=(
                'Hi Priya, happy to help. I\'ll bring common mixer parts so the job '
                'can be done in one visit. Available this Thursday or Friday.'
            ),
        )
        
        # Mereani's quote (accepted)
        q_mereani = Quote.objects.create(
            task=t2, tradie=mereani, price=110,
            base_price=110,
            include_platform_fee=False,
            estimated_job_duration='4 hours',
            message=(
                'Bula Adi! I\'d love to help. I\'ll bring all my own supplies including '
                'eco-friendly products. I can do a full deep clean in about 4 hours. '
                'Tuesday at 9 am works perfectly.'
            ),
            status=Quote.STATUS_ACCEPTED,
        )
        
        # Rajesh's quote on completed task
        q_rajesh = Quote.objects.create(
            task=t3, tradie=rajesh, price=420,
            base_price=400,  # Tradie wants FJD $400
            include_platform_fee=True,  # Tradie includes the fee
            estimated_platform_fee=Decimal('20.00'),
            platform_fee_rate_at_quote_time=Decimal('5.0'),  # Was 5% at time of quote
            platform_fee_cap_at_quote_time=Decimal('75.00'),
            client_quote_total=Decimal('420.00'),
            estimated_tradie_take_home=Decimal('400.00'),
            estimated_job_duration='Full day (8 hours)',
            warranty_or_followup_included=True,
            message=(
                'Bula John. This is a standard residential switchboard upgrade plus AC install. '
                'I can install a new 12-way Hager RCD board to FEA spec plus a quality '
                'split-system air con. Price includes boards, breakers, AC unit, and labour. '
                'Usually takes a full day.'
            ),
            status=Quote.STATUS_ACCEPTED,
        )

        self.stdout.write('Creating messages…')
        Message.objects.create(
            task=t2, sender=adi, recipient=mereani,
            body='Bula Mereani! Can you come Tuesday at 9 am?',
        )
        Message.objects.create(
            task=t2, sender=mereani, recipient=adi,
            body='Bula! Yes, Tuesday 9 am works perfectly for me. See you then 😊',
        )
        Message.objects.create(
            task=t2, sender=adi, recipient=mereani,
            body='Vinaka! I\'ll leave the spare key under the mat near the front door.',
        )

        self.stdout.write('Creating reviews…')
        # John rates Rajesh (public) — 6 criteria including timeline_schedule_delivery
        PublicReview.objects.create(
            task=t3, rater=john, ratee=rajesh,
            reliability_punctuality=5, quote_price_accuracy=5, value_for_money=4,
            service_quality_workmanship=5, communication_after_service=5, timeline_schedule_delivery=5,
            comment=(
                'Rajesh was outstanding. Arrived exactly on time, explained every step '
                'clearly, and the new switchboard looks and works perfectly. '
                'AC unit was installed professionally. Cleaned up the workspace before he left. '
                'Completed within the agreed 1-day timeframe. Vinaka vakalevu, Rajesh!'
            ),
        )
        # Rajesh rates John (private — dispute records only)
        PrivateReview.objects.create(
            task=t3, rater=rajesh, ratee=john,
            access_readiness=5, scope_clarity=5,
            communication=5, payment=5, conduct=5,
            comment='John was very well prepared. Site was ready, payment was prompt.',
        )

        self.stdout.write('Creating platform fees and invoices…')
        self._create_platform_fees_and_invoices(rajesh, t3)

        self.stdout.write('Creating sponsor banners…')
        self._create_sponsors()

        self.stdout.write('Creating quoting appointment and materials/inclusions scenarios…')
        self._create_appointment_and_materials_scenarios(priya, adi, john)

        self.stdout.write(self.style.SUCCESS(
            '\nSeed complete! Sample accounts (password: testpass123):\n'
            '  sailosi.tora@example.fj  — Tradie (plumber, Suva)\n'
            '  mereani.v@example.fj     — Tradie (cleaner, Nadi)\n'
            '  rajesh.kumar@example.fj  — Tradie (electrician, Lautoka)\n'
            '  etuate.waqa@example.fj   — Tradie (builder, Suva)\n'
            '  litia.rabuka@example.fj  — Tradie (event decorator/caterer, Nadi)\n'
            '  pita.volavola@example.fj — Tradie (mechanic, Lautoka)\n'
            '  ana.senikau@example.fj   — Tradie (gardener, Labasa)\n'
            '  mere.tuilagi@example.fj  — Tradie (caterer, Nausori)\n'
            '  priya.sharma@example.fj  — Client (Suva)\n'
            '  adi.litia@example.fj     — Client (Nadi)\n'
            '  john.whippy@example.fj   — Client (Lautoka)\n'
            '  mosese.ratu@example.fj   — Client (Labasa)\n'
            '  salote.naivalu@example.fj — Client (Nausori)\n'
            '\nPlatform fee settings: 7.5% with FJD $75 cap (3% on jobs over FJD $5,000)\n'
            'Sample platform fees created for completed task.\n'
            'Etuate Waqa has 2 completed jobs with pending platform fees, ready '
            'for an admin to fetch into a new invoice (one standard, one large job).\n'
            'Sample invoices and sponsors created.\n'
            'Sample quoting appointments created (requested, accepted, declined, '
            'and a flagged monitoring example).\n'
        ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _create_categories(self):
        """Create TradeCategory records from TRADE_CHOICES."""
        from marketplace.constants import TRADE_CHOICES
        
        for slug, label in TRADE_CHOICES:
            TradeCategory.objects.get_or_create(
                slug=slug,
                defaults={'name': label.split(' ', 1)[-1] if ' ' in label else label, 'icon': label[0]},
            )

    def _create_platform_settings(self):
        """Create platform settings."""
        PlatformSettings.objects.get_or_create(
            active=True,
            defaults={
                'success_fee_rate': Decimal('7.5'),
                'success_fee_cap': Decimal('75.00'),
            },
        )

    def _create_platform_fees_and_invoices(self, tradie, completed_task):
        """Create platform fees and invoices for demonstration."""
        # Create platform fee for the completed task
        pf = PlatformFee.objects.create(
            task=completed_task,
            tradie=tradie,
            final_job_value=Decimal('420.00'),
            fee_rate=Decimal('7.5'),
            fee_cap=Decimal('75.00'),
            fee_amount=Decimal('31.50'),  # 7.5% of 420
            status=PlatformFee.STATUS_INVOICED,
        )

        # Create a sample invoice with this fee
        invoice = Invoice.objects.create(
            tradie=tradie,
            invoice_number=f'INV-{timezone.now().strftime("%Y%m%d")}-{str(tradie.id).zfill(5)}-001',
            total_amount=Decimal('31.50'),
            status=Invoice.STATUS_SENT,
            due_date=timezone.now().date() + datetime.timedelta(days=7),
        )

        InvoiceLine.objects.create(
            invoice=invoice,
            platform_fee=pf,
            description=f'Platform fee for task: {completed_task.title}',
            amount=Decimal('31.50'),
        )

    def _create_sponsors(self):
        """Create sample sponsor banners."""
        today = timezone.now().date()
        
        sponsors_data = [
            {
                'business_name': 'Fiji Home Depot',
                'destination_url': 'https://www.fijhomedepot.example.fj',
                'placement': 'homepage',
                'start_date': today - datetime.timedelta(days=30),
                'end_date': today + datetime.timedelta(days=60),
            },
            {
                'business_name': 'Suva Hardware & Building Supplies',
                'destination_url': 'https://www.suva-hardware.example.fj',
                'placement': 'browse_tasks_sidebar',
                'start_date': today,
                'end_date': today + datetime.timedelta(days=90),
            },
            {
                'business_name': 'Nadi Electrical Supply',
                'destination_url': 'https://www.nadi-electrical.example.fj',
                'placement': 'task_detail_sidebar',
                'start_date': today - datetime.timedelta(days=7),
                'end_date': today + datetime.timedelta(days=30),
            },
        ]
        
        for data in sponsors_data:
            Sponsor.objects.get_or_create(
                business_name=data['business_name'],
                placements=[data['placement']],
                defaults={
                    'destination_url': data['destination_url'],
                    'start_date': data['start_date'],
                    'end_date': data['end_date'],
                    'active': True,
                    'banner_image': 'sponsors/placeholder.png',  # Will need actual image
                },
            )
            self.stdout.write(f'  Created sponsor: {data["business_name"]}')

    def _create_appointment_and_materials_scenarios(self, priya, adi, john):
        """
        Create tasks demonstrating materials/job inclusions and quoting
        appointments: a multi-slot request, an accepted appointment, a
        declined appointment, and a flagged off-platform monitoring example.
        """
        today = datetime.date.today()

        etuate = self._make_tradie(
            email='etuate.waqa@example.fj',
            first='Etuate', last='Waqa',
            town='Suva', mobile='+679 999 1122',
            biz='Waqa Building & Renovations',
            exp='15+ years',
            bio=(
                'Licensed builder specialising in home renovations and extensions '
                'across Suva and Nausori — bathrooms, kitchens and full home '
                'extensions, from planning through to finishing.'
            ),
            trades=['building', 'carpentry'],
            service_towns=['Suva', 'Nausori'],
        )
        litia = self._make_tradie(
            email='litia.rabuka@example.fj',
            first='Litia', last='Rabuka',
            town='Nadi', mobile='+679 888 3344',
            biz='Rabuka Events & Decor',
            exp='3-7 years',
            bio=(
                'Wedding and event decorator based in Nadi. I provide table '
                'settings, floral arrangements, backdrops and full venue styling '
                'for weddings, birthdays and corporate functions.'
            ),
            trades=['other', 'chef'],
            service_towns=['Nadi', 'Lautoka'],
        )
        pita = self._make_tradie(
            email='pita.volavola@example.fj',
            first='Pita', last='Volavola',
            town='Lautoka', mobile='+679 777 5566',
            biz='Volavola Auto Services',
            exp='7-15 years',
            bio=(
                'Mobile mechanic based in Lautoka. I diagnose and repair engine, '
                'brake and electrical issues for cars, vans and utes across the '
                'Western Division, with my own tools and diagnostic equipment.'
            ),
            trades=['mechanic'],
            service_towns=['Lautoka', 'Nadi'],
        )
        ana = self._make_tradie(
            email='ana.senikau@example.fj',
            first='Ana', last='Senikau',
            town='Labasa', mobile='+679 666 7788',
            biz='Senikau Garden Services',
            exp='1-3 years',
            bio=(
                'Gardening and lawn care service based in Labasa — lawn mowing, '
                'hedge trimming, garden clean-ups and rubbish removal for '
                'residential properties around Vanua Levu.'
            ),
            trades=['gardening'],
            service_towns=['Labasa'],
        )
        mere = self._make_tradie(
            email='mere.tuilagi@example.fj',
            first='Mere', last='Tuilagi',
            town='Nausori', mobile='+679 555 9900',
            biz='Tuilagi Catering',
            exp='3-7 years',
            bio=(
                'Catering service based in Nausori offering Fijian and Indian '
                'dishes for family functions, weddings and office events across '
                'Suva and Nausori.'
            ),
            trades=['chef', 'baker'],
            service_towns=['Nausori', 'Suva'],
        )

        mosese = self._make_client(
            email='mosese.ratu@example.fj',
            first='Mosese', last='Ratu',
            town='Labasa', mobile='+679 944 1212',
        )
        salote = self._make_client(
            email='salote.naivalu@example.fj',
            first='Salote', last='Naivalu',
            town='Nausori', mobile='+679 933 2323',
        )

        # Task 5 — Bathroom renovation, Suva — on-site quote, provider to advise after inspection
        t5 = Task.objects.create(
            client=priya, title='Bathroom renovation – full retile and fittings',
            category='building',
            description=(
                'Need a full bathroom renovation in our Suva home — replacing tiles, '
                'vanity, toilet and shower screen. The space is approximately 4m². '
                'There may be plumbing and waterproofing work involved, so we would '
                'like a tradie to inspect before giving a final quote.'
            ),
            budget=3500, town='Suva',
            preferred_date=today + datetime.timedelta(days=14),
            materials_responsibility='provider_to_advise_after_inspection',
            on_site_inspection_required=True,
            client_provide_photos=True,
        )
        t5.categories.set(TradeCategory.objects.filter(slug='building'))

        # Task 6 — Wedding decoration, Nadi — appointment, meals/refreshments provided
        t6 = Task.objects.create(
            client=adi, title='Wedding reception decoration – 80 guests',
            category='other',
            description=(
                'Looking for a decorator for a wedding reception at our home in Nadi. '
                'Need table settings, floral arrangements and a backdrop for around '
                '80 guests. Happy to walk through the layout on-site before quoting.'
            ),
            budget=1200, town='Nadi',
            preferred_date=today + datetime.timedelta(days=21),
            materials_responsibility='provider_should_supply',
            meals_provided=True,
            on_site_inspection_required=True,
        )
        t6.categories.set(TradeCategory.objects.filter(slug='other'))

        # Task 7 — Mechanic inspection, Lautoka — provider tools/equipment required
        t7 = Task.objects.create(
            client=john, title='Brake noise diagnosis – Toyota Hilux',
            category='mechanic',
            description=(
                'My 2014 Toyota Hilux has been making a grinding noise when braking. '
                'Need a mechanic to inspect and diagnose the issue before quoting for '
                'parts and labour.'
            ),
            budget=150, town='Lautoka',
            preferred_date=today + datetime.timedelta(days=4),
            materials_responsibility='provider_to_advise_after_inspection',
            tools_required=True,
            on_site_inspection_required=True,
        )
        t7.categories.set(TradeCategory.objects.filter(slug='mechanic'))

        # Task 8 — Garden clean-up, Labasa — rubbish removal + clean-up (flagged monitoring example)
        t8 = Task.objects.create(
            client=mosese, title='Overgrown section clean-up',
            category='gardening',
            description=(
                'Overgrown section needs a full clean-up — cutting back hedges, '
                'mowing the lawn and removing all green waste. Approx 600m² section '
                'in Labasa.'
            ),
            budget=180, town='Labasa',
            preferred_date=today + datetime.timedelta(days=5),
            materials_responsibility='not_applicable',
            rubbish_removal_required=True,
            clean_up_required=True,
        )
        t8.categories.set(TradeCategory.objects.filter(slug='gardening'))

        # Task 9 — Catering job, Nausori — site access + parking available
        t9 = Task.objects.create(
            client=salote, title='Catering for family function – approx 50 guests',
            category='chef',
            description=(
                'Need catering for a family function for around 50 people in Nausori. '
                'Fijian and Indian dishes preferred. Good vehicle access and parking '
                'available for delivery and set-up.'
            ),
            budget=900, town='Nausori',
            preferred_date=today + datetime.timedelta(days=10),
            materials_responsibility='provider_should_supply',
            site_access_available=True,
            parking_available_flag=True,
        )
        t9.categories.set(TradeCategory.objects.filter(slug='chef'))

        # Appointment 1 — multiple proposed slots, requested (bathroom renovation)
        appt1 = QuotingAppointment.objects.create(
            task=t5, client=priya, provider=etuate,
            appointment_note=(
                "I'd like to inspect the bathroom before quoting, as waterproofing "
                "and plumbing work can affect the price. Please choose one of these "
                "times."
            ),
        )
        for d, s, e in [
            (3, (9, 0), (10, 0)),
            (3, (14, 0), (15, 0)),
            (4, (10, 0), (11, 0)),
        ]:
            QuotingAppointmentSlot.objects.create(
                quoting_appointment=appt1,
                proposed_date=today + datetime.timedelta(days=d),
                start_time=datetime.time(*s),
                end_time=datetime.time(*e),
            )

        # Appointment 2 — accepted, with one selected slot (wedding decoration)
        appt2 = QuotingAppointment.objects.create(
            task=t6, client=adi, provider=litia,
            appointment_note=(
                'Happy to come view the venue space and discuss decor options '
                'before finalising the quote.'
            ),
            status=QuotingAppointment.STATUS_ACCEPTED,
        )
        slot2a = QuotingAppointmentSlot.objects.create(
            quoting_appointment=appt2,
            proposed_date=today + datetime.timedelta(days=5),
            start_time=datetime.time(10, 0), end_time=datetime.time(11, 0),
            is_selected=True,
        )
        QuotingAppointmentSlot.objects.create(
            quoting_appointment=appt2,
            proposed_date=today + datetime.timedelta(days=5),
            start_time=datetime.time(15, 0), end_time=datetime.time(16, 0),
        )
        QuotingAppointmentSlot.objects.create(
            quoting_appointment=appt2,
            proposed_date=today + datetime.timedelta(days=6),
            start_time=datetime.time(9, 0), end_time=datetime.time(10, 0),
        )
        appt2.selected_slot = slot2a
        appt2.save()

        # Appointment 3 — declined (mechanic inspection)
        appt3 = QuotingAppointment.objects.create(
            task=t7, client=john, provider=pita,
            appointment_note=(
                'I can take a look at the vehicle at these times to diagnose the '
                'brake issue before quoting for parts and labour.'
            ),
            status=QuotingAppointment.STATUS_DECLINED,
        )
        for d, s, e in [
            (2, (8, 0), (9, 0)),
            (2, (13, 0), (14, 0)),
        ]:
            QuotingAppointmentSlot.objects.create(
                quoting_appointment=appt3,
                proposed_date=today + datetime.timedelta(days=d),
                start_time=datetime.time(*s),
                end_time=datetime.time(*e),
            )

        # Appointment 4 — accepted, then task cancelled before quote acceptance
        # (flagged for backdoor/off-platform monitoring)
        appt4 = QuotingAppointment.objects.create(
            task=t8, client=mosese, provider=ana,
            appointment_note=(
                'I can come have a look at the section size and overgrowth before '
                'quoting for the clean-up.'
            ),
            status=QuotingAppointment.STATUS_ACCEPTED,
        )
        slot4a = QuotingAppointmentSlot.objects.create(
            quoting_appointment=appt4,
            proposed_date=today + datetime.timedelta(days=2),
            start_time=datetime.time(7, 0), end_time=datetime.time(8, 0),
            is_selected=True,
        )
        QuotingAppointmentSlot.objects.create(
            quoting_appointment=appt4,
            proposed_date=today + datetime.timedelta(days=3),
            start_time=datetime.time(7, 0), end_time=datetime.time(8, 0),
        )
        appt4.selected_slot = slot4a
        appt4.save()

        # Quotes showing quote_includes for the client to review
        Quote.objects.create(
            task=t6, tradie=litia, price=950,
            base_price=950,
            include_platform_fee=False,
            customer_facing_quote=Decimal('950.00'),
            client_quote_total=Decimal('950.00'),
            includes_materials=True,
            quote_includes='service_plus_products',
            estimated_job_duration='1 day setup',
            message=(
                'Bula Adi! After seeing the venue, I can put together table '
                'settings, floral arrangements and a backdrop for 80 guests. Price '
                'includes all flowers, fabric and decor items, with set-up the '
                'morning of the event.'
            ),
        )
        Quote.objects.create(
            task=t7, tradie=pita, price=120,
            base_price=120,
            include_platform_fee=False,
            customer_facing_quote=Decimal('120.00'),
            client_quote_total=Decimal('120.00'),
            quote_includes='materials_to_be_confirmed_after_inspection',
            estimated_job_duration='1 hour',
            message=(
                'Hi John, this covers the diagnosis and labour to identify the '
                'issue. If pads or rotors need replacing, I will confirm parts '
                'pricing once I have inspected the brakes.'
            ),
        )
        Quote.objects.create(
            task=t9, tradie=mere, price=850,
            base_price=850,
            include_platform_fee=False,
            customer_facing_quote=Decimal('850.00'),
            client_quote_total=Decimal('850.00'),
            includes_materials=True,
            quote_includes='service_plus_products',
            estimated_job_duration='Half day',
            message=(
                'Bula Salote! I can prepare a mix of Fijian and Indian dishes for '
                '50 guests, including delivery and set-up. Price includes all '
                'food, serving equipment and delivery.'
            ),
        )

        # Cancel the garden clean-up task after the appointment was accepted but
        # before any quote was accepted — triggers the backdoor monitoring flag.
        t8.status = Task.STATUS_CANCELLED
        t8.removed_at = timezone.now()
        t8.removed_by = mosese
        t8.removal_reason = 'Client said the work was no longer required.'
        t8.cancellation_reason = (
            'Client cancelled shortly after the quoting appointment was confirmed.'
        )
        t8.save()

        # ── Invoicing scenario ──────────────────────────────────────────────
        # Two completed jobs for Etuate within the last 14 days, with pending
        # platform fees ready for an admin to "Fetch completed jobs" into a
        # new invoice — one standard job (fee capped at FJD $75) and one
        # large job (Large job 3% fee rule).
        t10 = Task.objects.create(
            client=priya, title='Bathroom renovation quote - Suva',
            category='building',
            description=(
                'Full bathroom renovation completed — retiling, new vanity, '
                'toilet and shower screen, plus waterproofing.'
            ),
            budget=1200, town='Suva',
            preferred_date=today - datetime.timedelta(days=14),
            status=Task.STATUS_COMPLETED,
            assigned_tradie=etuate,
            final_job_value=Decimal('1200.00'),
            completed_at=timezone.now() - datetime.timedelta(days=12),
        )
        t10.categories.set(TradeCategory.objects.filter(slug='building'))
        create_platform_fee_for_task(t10, t10.final_job_value)

        t11 = Task.objects.create(
            client=adi, title='Kitchen renovation - Nadi',
            category='building',
            description=(
                'Full kitchen renovation completed — new cabinetry, benchtops, '
                'splashback tiling and appliance installation.'
            ),
            budget=8000, town='Nadi',
            preferred_date=today - datetime.timedelta(days=8),
            status=Task.STATUS_COMPLETED,
            assigned_tradie=etuate,
            final_job_value=Decimal('8000.00'),
            completed_at=timezone.now() - datetime.timedelta(days=6),
        )
        t11.categories.set(TradeCategory.objects.filter(slug='building'))
        create_platform_fee_for_task(t11, t11.final_job_value)

        # Every Task/Quote/PlatformFee/Invoice/Review above was created via
        # direct .objects.create() calls (not the real views/utils write
        # paths), so none of them got their new workspace FK populated —
        # backfill them the same way a production migration would, rather
        # than hand-editing every creation call above.
        self.stdout.write('Linking workspace ownership…')
        call_command('audit_workspace_relationships', repair=True)

    def _make_tradie(self, email, first, last, town, mobile,
                     biz='', tin='', exp='', bio='', trades=None, service_towns=None):
        user, created = User.objects.get_or_create(
            email=email,
            defaults=dict(
                first_name=first, last_name=last,
                town=town, mobile=mobile, role=User.ROLE_TRADIE,
            ),
        )
        if created:
            user.set_password(PASSWORD)
            user.save()
        TradieProfile.objects.get_or_create(
            user=user,
            defaults=dict(
                business_name=biz, tin=tin,
                years_experience=exp, bio=bio,
                trades=trades or [], service_towns=service_towns or [],
            ),
        )
        workspaces.ensure_user_capability(user, can_offer_services=True)
        workspaces.create_client_workspace(user)
        workspaces.create_individual_provider_workspace(user)
        if created:
            self.stdout.write(f'  Created tradie: {user}')
        return user

    def _make_client(self, email, first, last, town, mobile):
        user, created = User.objects.get_or_create(
            email=email,
            defaults=dict(
                first_name=first, last_name=last,
                town=town, mobile=mobile, role=User.ROLE_CLIENT,
            ),
        )
        if created:
            user.set_password(PASSWORD)
            user.save()
            self.stdout.write(f'  Created client: {user}')
        workspaces.ensure_user_capability(user)
        workspaces.create_client_workspace(user)
        return user
