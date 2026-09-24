"""
Local pages: "Plumbers in Karori" — one page per trade and area.

These exist so someone searching for a tradie in their own suburb finds Level.
Each page is written for that trade: the jobs people actually post, what to
check before hiring, and the licence rule where one applies. Nothing here
invents numbers — live counts and prices come from the database, and only show
once there's enough to be worth showing.
"""

# Per trade: a one-line description, the jobs customers post most, and what to
# check before hiring. Licence wording comes from the category itself.
TRADES = {
    'builder': {
        'blurb': 'Builders take on everything from rotten weatherboards to a new room off the back.',
        'jobs': ['Replace rotten weatherboards or framing', 'Build or repair a deck',
                 'Extension or a new room', 'Reclad or re-line a wall', 'Repair storm or water damage'],
        'checks': ['Ask for their LBP number and check it on the public register.',
                   'For work over $30,000 including GST, the law says you get a written contract, a disclosure statement and a checklist before work starts.',
                   'Ask who will actually be on site — the person quoting isn’t always the person building.'],
    },
    'carpenter': {
        'blurb': 'Carpenters and joiners do the finishing work: doors, trim, shelving, stairs and built-in furniture.',
        'jobs': ['Hang or re-hang doors', 'Built-in wardrobes or shelving', 'Skirtings, architraves and trim',
                 'Repair or rebuild stairs', 'Fit out a room or office'],
        'checks': ['Ask to see photos of finished work like yours.',
                   'Check whether materials are included in the price.',
                   'Agree who fills, sands and paints afterwards.'],
    },
    'electrician': {
        'blurb': 'Electricians handle anything on the wiring: new points, lights, switchboards and faults.',
        'jobs': ['Add power points or outdoor sockets', 'Replace a switchboard or fix a tripping breaker',
                 'Install downlights or LED lighting', 'Wire a heat pump, oven or hob', 'EV charger installation'],
        'checks': ['Only a registered electrical worker can do this — ask for their registration number.',
                   'Ask for a Certificate of Compliance when the job is done. You’re entitled to one.',
                   'For older homes, ask whether the switchboard can take the extra load.'],
    },
    'plumber': {
        'blurb': 'Plumbers cover water in and water out: taps, cylinders, leaks, bathrooms and blockages.',
        'jobs': ['Fix a leaking tap, toilet or pipe', 'Replace a hot water cylinder',
                 'Bathroom or kitchen plumbing', 'Install a new laundry or outdoor tap', 'Track down a hidden leak'],
        'checks': ['Sanitary plumbing must be done by a registered plumber — ask for the number.',
                   'For emergencies, ask the call-out fee and the hourly rate before they come.',
                   'Ask whether the price covers making good — patching linings and tiles afterwards.'],
    },
    'gasfitter': {
        'blurb': 'Gasfitters install and certify gas appliances, lines and hot water.',
        'jobs': ['Install a gas hob or cooktop', 'Gas hot water — continuous flow or cylinder',
                 'Run a new gas line or bottle changeover', 'Service or repair a gas heater', 'Gas safety check'],
        'checks': ['Gasfitting must be done by a registered gasfitter. Ask for the number.',
                   'Ask for a gas safety certificate on completion.',
                   'Check whether the appliance itself is in the quote or supplied by you.'],
    },
    'drainlayer': {
        'blurb': 'Drainlayers deal with what’s under the ground: blocked, broken or new drains.',
        'jobs': ['Clear a blocked drain', 'CCTV camera inspection', 'Replace broken or collapsed pipe',
                 'New stormwater or sewer connection', 'Fix a soggy or flooding section'],
        'checks': ['Drainlaying must be done by a registered drainlayer.',
                   'Ask for the camera footage — you paid for it and it shows what was wrong.',
                   'Council consent is often needed for new connections. Ask who arranges it.'],
    },
    'roofer': {
        'blurb': 'Roofers repair and replace roofs, spouting and flashings.',
        'jobs': ['Fix a leak or broken flashing', 'Replace the roof', 'New spouting and downpipes',
                 'Roof wash, repaint or re-coat', 'Storm damage repair'],
        'checks': ['Ask how they’ll access the roof safely — scaffolding or edge protection may be in the price, or not.',
                   'Get the warranty in writing: one for the material, one for the workmanship.',
                   'For a full re-roof, ask whether the underlay and any rotten purlins are included.'],
    },
    'bricklayer': {
        'blurb': 'Bricklayers, blocklayers and stonemasons build and repair brick, block and stone.',
        'jobs': ['Repair or repoint brickwork', 'Block retaining wall', 'Brick or stone feature wall',
                 'Rebuild a chimney', 'Paving and stone paths'],
        'checks': ['Structural brick and block is restricted building work and needs an LBP.',
                   'Retaining walls over 1.5 m usually need council consent. Ask who handles it.',
                   'Ask for a sample or photo of the brick and mortar colour before they start.'],
    },
    'plasterer': {
        'blurb': 'Plasterers and GIB stoppers line, stop and finish interior walls, and plaster exteriors.',
        'jobs': ['GIB and stopping after a renovation', 'Patch and repair holes or cracks',
                 'Skim coat an old wall or ceiling', 'Exterior plaster repair', 'Level 4 or level 5 finish before painting'],
        'checks': ['Ask which finish level you’re quoted for — level 5 costs more and matters under strong light.',
                   'Exterior plastering that is restricted building work needs an LBP.',
                   'Check whether sanding dust clean-up is included.'],
    },
    'painter': {
        'blurb': 'Painters and decorators do interiors, exteriors, roofs and the prep that makes them last.',
        'jobs': ['Repaint the inside of a house', 'Exterior repaint', 'Roof repaint',
                 'Fences, decks and outdoor staining', 'Wallpaper strip and re-hang'],
        'checks': ['Ask how many coats and which brand and product — that’s where cheap quotes cut corners.',
                   'Prep is most of the job. Ask what filling, sanding and washing is included.',
                   'For houses built before 2000, ask how they’ll handle possible lead paint.'],
    },
    'tiler': {
        'blurb': 'Tilers lay wall and floor tiles, and waterproof wet areas before they do.',
        'jobs': ['Tile a bathroom or shower', 'Kitchen splashback', 'Tile a floor or entrance',
                 'Re-grout or re-seal', 'Fix loose or drummy tiles'],
        'checks': ['Wet areas must be waterproofed to the Building Code. Ask who does it and ask for the producer statement.',
                   'Ask whether tile supply is included, and who pays for breakages and cuts.',
                   'Agree the grout colour and tile layout before they start.'],
    },
    'flooring': {
        'blurb': 'Flooring installers lay carpet, vinyl, timber and laminate, and sand and finish floors.',
        'jobs': ['Carpet a house or a room', 'Vinyl or hybrid flooring', 'Sand and polish timber floors',
                 'Laminate or engineered timber', 'Lift old flooring and level the subfloor'],
        'checks': ['Ask whether uplift and disposal of the old floor is in the price.',
                   'Subfloor levelling is the usual surprise cost. Ask them to check before quoting.',
                   'For timber floors, ask how many coats and how long before furniture goes back.'],
    },
    'kitchen-bath': {
        'blurb': 'Kitchen and bathroom specialists manage the whole renovation, trades and all.',
        'jobs': ['Full bathroom renovation', 'New kitchen', 'Replace a bath with a shower',
                 'Laundry makeover', 'Update benchtops and cabinetry'],
        'checks': ['Ask who manages the other trades — plumber, sparky, tiler — and whether they’re in the price.',
                   'Get the timeline in writing: how long the room is out of action.',
                   'Renovations over $30,000 including GST need a written contract by law.'],
    },
    'landscaper': {
        'blurb': 'Landscapers build and plant outdoor areas: paving, decks, retaining, lawns and gardens.',
        'jobs': ['Paving or a patio', 'Retaining wall', 'New lawn or re-turf',
                 'Garden design and planting', 'Tidy up and clear an overgrown section'],
        'checks': ['Ask for a planting plan and whether plants are included.',
                   'Retaining over 1.5 m, or holding up a driveway, usually needs consent and an engineer.',
                   'Agree who removes the spoil and green waste.'],
    },
    'fencer': {
        'blurb': 'Fencers build and repair fences, gates and posts.',
        'jobs': ['New timber or paling fence', 'Replace rotten posts or palings', 'Gate — timber or metal',
                 'Pool fencing that meets the rules', 'Retaining and fence combination'],
        'checks': ['A shared boundary fence is usually a half-half cost with your neighbour under the Fencing Act — talk to them first, in writing.',
                   'Pool fencing has its own rules and gets inspected by the council.',
                   'Ask about post depth and whether they concrete in.'],
    },
    'concreter': {
        'blurb': 'Concreters pour and finish driveways, floors, paths and foundations.',
        'jobs': ['Concrete driveway', 'Path or patio', 'Garage or shed floor',
                 'Exposed aggregate or coloured concrete', 'Cut out and replace cracked concrete'],
        'checks': ['Ask about thickness, reinforcing and the mix — that’s what stops cracking later.',
                   'Ask how they’ll handle drainage and fall so water runs away from the house.',
                   'A vehicle crossing onto the road needs council approval. Ask who applies.'],
    },
    'glazier': {
        'blurb': 'Glaziers replace broken glass and install windows, doors and splashbacks.',
        'jobs': ['Replace a broken window', 'Double glazing retrofit', 'Shower screen or mirror',
                 'Glass splashback', 'Repair a sticking or failed window seal'],
        'checks': ['Ask whether the glass meets safety glass rules for that spot — doors and low windows have rules.',
                   'For retrofit double glazing, ask whether your existing frames suit it.',
                   'Ask about board-up and make-safe if the glass is already broken.'],
    },
    'heat-pumps': {
        'blurb': 'Heat pump and ventilation installers size, fit and service heating and airflow systems.',
        'jobs': ['Install a heat pump', 'Ducted or multi-room system', 'Service or repair an existing unit',
                 'Home ventilation system', 'Move an outdoor unit'],
        'checks': ['Ask them to size the unit for the room, not just quote the cheapest model.',
                   'Installers need a refrigerant handling certificate, and the wiring must be done by a registered electrician.',
                   'Check what the warranty covers and who services it.'],
    },
    'insulation': {
        'blurb': 'Insulation installers fit ceiling, underfloor and wall insulation.',
        'jobs': ['Ceiling insulation top-up', 'Underfloor insulation', 'Remove old or damp insulation',
                 'Wall insulation during a renovation', 'Moisture barrier under the house'],
        'checks': ['Ask for the R-value being installed, not just "insulation".',
                   'Rentals must meet the Healthy Homes standards — ask for the statement of compliance.',
                   'Ask whether removal and disposal of the old material is included.'],
    },
    'arborist': {
        'blurb': 'Arborists prune, fell and care for trees, safely and legally.',
        'jobs': ['Fell a tree', 'Prune or reduce a large tree', 'Remove a stump',
                 'Clear branches off the roof or power lines', 'Storm damage clean-up'],
        'checks': ['Check whether the tree is protected by the council before anyone cuts it.',
                   'Ask for proof of public liability insurance — this is high-risk work near houses.',
                   'Agree whether they chip and take away the waste, or leave it for firewood.'],
    },
    'demolition': {
        'blurb': 'Demolition crews take down buildings and structures and clear the site.',
        'jobs': ['Demolish a garage or shed', 'Strip out a kitchen or bathroom', 'Remove a chimney',
                 'Take down a house', 'Clear a site before building'],
        'checks': ['Buildings from before 2000 need an asbestos check before work starts. Ask who arranges it.',
                   'Ask what happens to the waste and whether disposal fees are in the price.',
                   'Demolition usually needs a consent. Ask who applies for it.'],
    },
    'earthworks': {
        'blurb': 'Earthworks contractors dig, level, drain and move ground.',
        'jobs': ['Level or benched a sloping section', 'Dig footings or a foundation',
                 'Trenching for services', 'Driveway base and metal', 'Fix drainage and flooding'],
        'checks': ['Ask them to check service locations before digging — hitting a cable or gas line is expensive.',
                   'Earthworks over a certain volume need council consent. Ask who handles it.',
                   'Agree where the spoil goes and who pays to cart it away.'],
    },
    'scaffolding': {
        'blurb': 'Scaffolders put up safe access for work at height, and take it down after.',
        'jobs': ['Scaffold for a re-roof or repaint', 'Edge protection for a new build',
                 'Mobile tower for a short job', 'Extend or alter existing scaffold', 'Weekly hire extension'],
        'checks': ['Ask what the weekly hire rate is after the first period — that’s where costs creep.',
                   'Scaffolding over 5 m needs a certificate of competence. Ask to see it.',
                   'Agree who arranges a council permit if it goes over a footpath.'],
    },
    'handyman': {
        'blurb': 'Handymen take on the small jobs that other trades won’t make a visit for.',
        'jobs': ['A list of small repairs around the house', 'Hang shelves, TVs or curtain rails',
                 'Flat-pack assembly', 'Fix a sticking door or window', 'Water blasting and odd outdoor jobs'],
        'checks': ['Handymen can’t legally do electrical, gas or sanitary plumbing work. For those, use the licensed trade.',
                   'Ask the hourly rate, the minimum charge and whether materials are extra.',
                   'Group small jobs into one visit — it’s cheaper than several call-outs.'],
    },
    'designer': {
        'blurb': 'Architectural designers and architects draw plans and get them through consent.',
        'jobs': ['Plans for an extension or new build', 'Consent drawings and council application',
                 'Concept design and feasibility', 'Minor dwelling or sleepout plans', 'Resource consent advice'],
        'checks': ['Design of restricted building work needs an LBP with a design licence, or a registered architect.',
                   'Ask what their fee covers: concept only, or through to consent and site visits.',
                   'Ask for examples of consented projects like yours with the same council.'],
    },
}


def content(slug):
    return TRADES.get(slug)


def pairs(categories, areas):
    """Every (category, area) that has page content, for the sitemap and the index."""
    return [(c, a) for c in categories if c['slug'] in TRADES for a in areas]
