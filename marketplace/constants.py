TRADE_CHOICES = [
    ('plumbing',    '🔧 Plumbing'),
    ('electrical',  '⚡ Electrical'),
    ('cleaning',    '🧹 Cleaning'),
    ('gardening',   '🌿 Gardening'),
    ('carpentry',   '🪚 Carpentry'),
    ('painting',    '🎨 Painting'),
    ('aircon',      '❄️ Air Conditioning'),
    ('removalist',  '🚛 Removalist'),
    ('building',    '🏗️ Building'),
    ('locksmith',   '🔒 Locksmith'),
    ('it',          '🖥️ IT / Tech'),
    ('pest',        '🐛 Pest Control'),
    ('other',       '🔨 Other'),
    ('chef',        '🍳 Chef'),
    ('baker',       '🥐 Baker'),
    ('mechanic',    '🛠️ Mechanic'),
    ('photographer','📸 Photographer'),
    ('makeup',      '💄 Makeup Artist'),
    ('nanny',       '👶 Nanny'),
    ('tutor',       '📚 Tutor'),
    ('swim_coach',  '🏊 Swim Coach'),
    ('joiner',      '🪵 Joiner'),
    ('tiler',       '🧱 Tiler'),
    ('roofer',      '🏠 Roofer'),
    ('plasterer',   '🪣 Plasterer'),
    ('welder',      '🔥 Welder'),
    ('fencing',     '🚧 Fencing'),
    ('solar',       '☀️ Solar Installation'),
    ('civil_works', '⛏️ Civil Works'),
    ('concreting',  '🪨 Concreting / Cement'),
    ('heavy_equipment', '🚜 Heavy Equipment'),
    ('whitegoods',  '🧊 Whitegoods'),
    ('small_appliance', '🔌 Small Appliance Repair'),
    ('laundry_tech', '🧺 Laundry Tech'),
]

TOWN_CHOICES = [
    ('Suva',    'Suva'),
    ('Nadi',    'Nadi'),
    ('Lautoka', 'Lautoka'),
    ('Nausori', 'Nausori'),
    ('Labasa',  'Labasa'),
]

EXPERIENCE_CHOICES = [
    ('< 1 year',   'Less than 1 year'),
    ('1-3 years',  '1–3 years'),
    ('3-7 years',  '3–7 years'),
    ('7-15 years', '7–15 years'),
    ('15+ years',  '15+ years'),
]

# Founding member program — the first N tradies to register get a badge and a
# platform fee credit, spent down automatically as their jobs complete.
FOUNDING_MEMBER_SLOTS = 20
FOUNDING_MEMBER_CREDIT = '200.00'

# Market founding seller program — the first N sellers (tradie or client) to
# post a Market listing get a platform fee credit, spent down automatically
# as their listings sell. Separate pool from the job founding-member program
# above since Market sellers aren't necessarily tradies.
MARKET_FOUNDING_SLOTS = 100
MARKET_FOUNDING_CREDIT = '100.00'

# ──────────────────────────────────────────────────────────────────────
# PUBLIC review criteria (client → provider).  Shown on provider profile.
# ──────────────────────────────────────────────────────────────────────
PUBLIC_REVIEW_CRITERIA = [
    {
        'field': 'reliability_punctuality',
        'label': 'Reliability & Punctuality',
        'descriptors': [
            (5, 'Arrived or delivered exactly as agreed, fully reliable'),
            (4, 'Minor delay or change, but communicated clearly'),
            (3, 'Some delay or uncertainty, but still followed through'),
            (2, 'Unreliable timing or repeated changes with poor notice'),
            (1, 'No-show, ghosted, or failed to attend/deliver as agreed'),
        ],
    },
    {
        'field': 'quote_price_accuracy',
        'label': 'Quote / Price Accuracy',
        'descriptors': [
            (5, 'Final price matched the quote exactly or was lower'),
            (4, 'Small change, clearly explained and agreed first'),
            (3, 'Moderate change, explained but not fully agreed upfront'),
            (2, 'Significant price change with weak explanation'),
            (1, 'Major surprise charges or bait-and-switch pricing'),
        ],
    },
    {
        'field': 'value_for_money',
        'label': 'Value for Money',
        'descriptors': [
            (5, 'Excellent value for the quality and service delivered'),
            (4, 'Fair value and in line with expectations'),
            (3, 'Acceptable value, but nothing special'),
            (2, 'Felt overpriced for what was delivered'),
            (1, 'Poor value or significantly overcharged'),
        ],
    },
    {
        'field': 'service_quality_workmanship',
        'label': 'Service Quality / Workmanship',
        'descriptors': [
            (5, 'Excellent result, exceeded expectations'),
            (4, 'Good, professional work/service with only minor issues'),
            (3, 'Acceptable result but with noticeable gaps or rough edges'),
            (2, 'Poor quality, incomplete, messy, or below expected standard'),
            (1, 'Unacceptable, defective, unsafe, unusable, or needs redoing'),
        ],
    },
    {
        'field': 'communication_after_service',
        'label': 'Communication & After-Service',
        'descriptors': [
            (5, 'Clear, proactive communication and excellent follow-up'),
            (4, 'Responsive and helpful when contacted'),
            (3, 'Basic communication, limited follow-up'),
            (2, 'Hard to reach or unclear communication'),
            (1, 'Disappeared, ignored concerns, or refused to address issues'),
        ],
    },
    {
        'field': 'timeline_schedule_delivery',
        'label': 'Timeline / Schedule Delivery',
        'descriptors': [
            (5, 'Completed within the agreed timeframe or earlier'),
            (4, 'Minor delay, clearly communicated and reasonably managed'),
            (3, 'Finished later than agreed, but delay was explained'),
            (2, 'Significant delay with poor communication'),
            (1, 'Failed to complete on time or abandoned the agreed schedule'),
        ],
    },
]

# ──────────────────────────────────────────────────────────────────────
# PRIVATE review criteria (tradie → client).
# ADMIN / DISPUTE RECORDS ONLY — never shown on any public page.
# ──────────────────────────────────────────────────────────────────────
PRIVATE_REVIEW_CRITERIA = [
    {
        'field': 'access_readiness',
        'label': 'Access & Readiness',
        'descriptors': [
            (5, 'Site ready to start, keys/access sorted before arrival'),
            (4, 'Minor delay to access, resolved quickly'),
            (3, 'Had to wait but client eventually arranged access'),
            (2, 'Significant delay; unprepared for the job scope'),
            (1, 'No access / refused entry / work couldn\'t begin'),
        ],
    },
    {
        'field': 'scope_clarity',
        'label': 'Scope Clarity',
        'descriptors': [
            (5, 'Scope fully defined upfront; no changes during job'),
            (4, 'Small additions, agreed & priced before starting'),
            (3, 'Scope crept but client was reasonable about it'),
            (2, 'Significant undisclosed work added mid-job'),
            (1, 'Completely different job to what was advertised'),
        ],
    },
    {
        'field': 'communication',
        'label': 'Communication',
        'descriptors': [
            (5, 'Replied promptly, clear and cooperative throughout'),
            (4, 'Generally responsive with minor delays'),
            (3, 'Slow at times but always replied eventually'),
            (2, 'Hard to reach; caused delays to the job'),
            (1, 'Unresponsive or hostile; blocked communication'),
        ],
    },
    {
        'field': 'payment',
        'label': 'Payment',
        'descriptors': [
            (5, 'Paid in full on time, exactly as agreed'),
            (4, 'Paid promptly; one minor clarification needed'),
            (3, 'Paid but needed a reminder or two'),
            (2, 'Late payment; needed significant chasing'),
            (1, 'Refused to pay agreed amount or disputed in bad faith'),
        ],
    },
    {
        'field': 'conduct',
        'label': 'Conduct on Site',
        'descriptors': [
            (5, 'Respectful, professional, made local professional feel welcome'),
            (4, 'Friendly and courteous throughout'),
            (3, 'Neutral; no issues but not particularly supportive'),
            (2, 'Dismissive or interfering; created friction'),
            (1, 'Hostile, abusive, or unsafe environment'),
        ],
    },
]
