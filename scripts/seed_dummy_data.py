"""Seed the database with DIN-relevant dummy contacts.

Only runs when ENTERPRISE_MODE=false. Refuses to run against a prod database.

Usage:
    python -m scripts.seed_dummy_data            # adds if empty, no-op otherwise
    python -m scripts.seed_dummy_data --reset    # wipes contacts + re-seeds

fly_status assignments reflect a plausible DIN priority pattern:
  - Must Fly: capital sources (SWFs), gov decision-makers, flagship deal CEOs
  - Fly List: useful operating partners, intermediaries, mid-priority LPs
  - Not Sure Yet: advisors and contacts still being evaluated
"""

from __future__ import annotations

import sys

from app.config import get_settings
from app.database import SessionLocal
from app.models.contact import Contact
from app.models.user import User

DUMMY_CONTACTS: list[dict[str, object]] = [
    {
        "name": "Marcus Sterling",
        "title": "Managing Partner",
        "company_name": "Ironclad Capital Partners",
        "email": "msterling@ironclad-cap.fake",
        "cell_phone": "555-0101",
        "primary_fund": "Maritime",
        "contact_type": "LP",
        "lp_subtype": "Family Office",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Must Fly",
        "sectors": ["Defense", "Maritime", "Port Infrastructure"],
        "notes": "Focused on defense shipyard acquisitions. Interested in Mobile, AL.",
    },
    {
        "name": "Diana Cho",
        "title": "Principal",
        "company_name": "Catalur Capital",
        "email": "dcho@catalur.fake",
        "cell_phone": "555-0102",
        "primary_fund": "Critical Minerals",
        "contact_type": "LP",
        "lp_subtype": "Family Office",
        "gender": "Female",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["Critical Minerals", "Lithium"],
        "notes": "Rare earth metals expertise. North American lithium projects.",
    },
    {
        "name": "Sarah Kowalski",
        "title": "CEO",
        "company_name": "Northern Titanium Corp",
        "email": "skowalski@northerntitanium.fake",
        "cell_phone": "555-0201",
        "primary_fund": "Critical Minerals",
        "contact_type": "Portfolio",
        "gender": "Female",
        "country": "Canada",
        "fly_status": "Must Fly",
        "sectors": ["Mining Operations", "Titanium"],
        "notes": "Titanium mine in Quebec. Exploring PE partnership for expansion.",
    },
    {
        "name": "James Blackwood",
        "title": "Owner",
        "company_name": "Blackwood Rare Earth Mining",
        "email": "jblackwood@blackwoodmining.fake",
        "cell_phone": "555-0202",
        "primary_fund": "Critical Minerals",
        "contact_type": "Portfolio",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Must Fly",
        "sectors": ["Mining Operations", "Rare Earth Processing", "Defense"],
        "notes": "Wyoming rare earth mine. DOD contracts. Expansion capital needed.",
    },
    {
        "name": "Elena Rodriguez",
        "title": "VP Operations",
        "company_name": "American Lithium Partners",
        "email": "erodriguez@americanlithium.fake",
        "cell_phone": "555-0203",
        "primary_fund": "Critical Minerals",
        "contact_type": "Portfolio",
        "gender": "Female",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["Lithium", "Mining Operations"],
        "notes": "Nevada lithium deposits. EV battery supply chain.",
    },
    {
        "name": "Admiral William Barrett (Ret.)",
        "title": "CEO",
        "company_name": "Mare Island Naval Shipyard LLC",
        "email": "wbarrett@mareislandnaval.fake",
        "cell_phone": "555-0301",
        "primary_fund": "Maritime",
        "contact_type": "Portfolio",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Must Fly",
        "ex_government": "Yes",
        "sectors": ["Shipbuilding", "Defense", "Automated Shipyards"],
        "notes": (
            "Former Navy. Reopening Mare Island shipyard. "
            "Needs $200M for modernization."
        ),
    },
    {
        "name": "Christine O'Neill",
        "title": "Managing Director",
        "company_name": "Mobile Shipbuilding Corp",
        "email": "coneill@mobileship.fake",
        "cell_phone": "555-0302",
        "primary_fund": "Maritime",
        "contact_type": "Portfolio",
        "gender": "Female",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["Shipbuilding", "Defense-Adjacent Repair"],
        "notes": "Port of Mobile dry dock. Defense contracts pipeline.",
    },
    {
        "name": "Kevin Park",
        "title": "CFO",
        "company_name": "Pacific Shipyards Group",
        "email": "kpark@pacificshipyards.fake",
        "cell_phone": "555-0303",
        "primary_fund": "Maritime",
        "contact_type": "Portfolio",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["Shipbuilding", "Port Infrastructure"],
        "notes": "San Diego shipyard. Commercial + defense mix.",
    },
    {
        "name": "Douglas Otto",
        "title": "Director",
        "company_name": "Alabama Port Authority",
        "email": "dotto@alabamaports.fake",
        "cell_phone": "555-0401",
        "primary_fund": "Maritime",
        "contact_type": "Government",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["Port Infrastructure", "Government"],
        "notes": "Port of Mobile director. P3 investment priorities.",
    },
    {
        "name": "Anthony Chen",
        "title": "Managing Director",
        "company_name": "Moelis & Company",
        "email": "achen@moelis.fake",
        "cell_phone": "555-0501",
        "primary_fund": "General",
        "contact_type": "Intermediary",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["Investment Banking", "Defense"],
        "notes": "M&A advisory for defense industrials. Shipyard deal pipeline.",
    },
    {
        "name": "Lisa Montenegro",
        "title": "Partner",
        "company_name": "Aurelius Capital Management",
        "email": "lmontenegro@aurelius.fake",
        "cell_phone": "555-0502",
        "primary_fund": "Critical Minerals",
        "contact_type": "Intermediary",
        "gender": "Female",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["Investment Banking", "Mining Operations"],
        "notes": "Distressed mining assets specialist. Past copper/nickel deals.",
    },
    {
        "name": "Dr. Jonathan Minerals",
        "title": "Senior Geologist",
        "company_name": "USGS Critical Minerals Group",
        "email": "jminerals@usgs.fake",
        "cell_phone": "555-0601",
        "primary_fund": "Critical Minerals",
        "contact_type": "Advisor",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Not Sure Yet",
        "sectors": ["Critical Minerals", "Government"],
        "notes": "USGS critical minerals expert. DOD supply chain advisor.",
    },
    {
        "name": "Captain Maria Santos (Ret.)",
        "title": "Maritime Consultant",
        "company_name": "Pacific Maritime Advisors",
        "email": "msantos@pacificmaritime.fake",
        "cell_phone": "555-0701",
        "primary_fund": "Maritime",
        "contact_type": "Advisor",
        "gender": "Female",
        "country": "United States",
        "fly_status": "Not Sure Yet",
        "ex_government": "Yes",
        "sectors": ["Shipbuilding", "Defense"],
        "notes": "Former Navy captain. Shipyard modernization consultant.",
    },
    {
        "name": "Sheikh Abdullah Al-Rashid",
        "title": "Investment Director",
        "company_name": "Abu Dhabi Sovereign Wealth Fund",
        "email": "aalrashid@adswf.fake",
        "cell_phone": "+971-55-000-0801",
        "primary_fund": "Energy",
        "contact_type": "LP",
        "lp_subtype": "Sovereign Wealth Fund",
        "gender": "Male",
        "country": "United Arab Emirates",
        "fly_status": "Must Fly",
        "sectors": ["LP / Capital", "AI Infrastructure", "Data Centers"],
        "notes": "Sovereign wealth LP. $5B infrastructure allocation.",
    },
    {
        "name": "Dr. Yuki Tanaka",
        "title": "Head of Alternatives",
        "company_name": "Japan Investment Corporation",
        "email": "ytanaka@jic.fake",
        "cell_phone": "+81-90-0000-0901",
        "primary_fund": "Energy",
        "contact_type": "LP",
        "lp_subtype": "Sovereign Wealth Fund",
        "gender": "Female",
        "country": "Japan",
        "fly_status": "Fly List",
        "sectors": ["LP / Capital", "Allied Manufacturing"],
        "notes": "Japanese government investment fund. US infrastructure partnerships.",
    },
    {
        "name": "Raj Patel",
        "title": "VP Data Center Strategy",
        "company_name": "Hyperscale Compute Inc",
        "email": "rpatel@hyperscale.fake",
        "cell_phone": "555-0801",
        "primary_fund": "Energy",
        "contact_type": "Portfolio",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["Data Centers", "AI Infrastructure", "Federal Site Energy"],
        "notes": "Data center developer. Federal site energy opportunities.",
    },
    # --- Added: US government, Saudi government, Saudi SWF, more women ---
    {
        "name": "Brigadier General Helen Marsh",
        "title": "Director, Industrial Base Policy",
        "company_name": "U.S. Department of Defense",
        "email": "hmarsh@dod.fake",
        "cell_phone": "555-0901",
        "primary_fund": "General",
        "contact_type": "Government",
        "gender": "Female",
        "country": "United States",
        "fly_status": "Must Fly",
        "sectors": ["Government", "Defense"],
        "notes": "OSD industrial base lead. Coordinates DPA Title III investments.",
    },
    {
        "name": "Robert Hayes",
        "title": "Senior Advisor",
        "company_name": "U.S. Department of Energy — Loan Programs Office",
        "email": "rhayes@doe.fake",
        "cell_phone": "555-0902",
        "primary_fund": "Energy",
        "contact_type": "Government",
        "gender": "Male",
        "country": "United States",
        "fly_status": "Must Fly",
        "sectors": ["Government", "Federal Site Energy"],
        "notes": "DOE LPO. Federal-site power generation pathways.",
    },
    {
        "name": "Khalid bin Saud",
        "title": "Director, Industrial Investments",
        "company_name": "Saudi Public Investment Fund (PIF)",
        "email": "kbsaud@pif.fake",
        "cell_phone": "+966-50-000-1001",
        "primary_fund": "Energy",
        "contact_type": "LP",
        "lp_subtype": "Sovereign Wealth Fund",
        "gender": "Male",
        "country": "Saudi Arabia",
        "fly_status": "Must Fly",
        "sectors": ["LP / Capital", "Industrial", "Data Centers"],
        "notes": "PIF industrials desk. Co-investment appetite for US energy infra.",
    },
    {
        "name": "Dr. Layla Al-Otaibi",
        "title": "Deputy Minister",
        "company_name": "Saudi Ministry of Energy",
        "email": "lalotaibi@moe.fake",
        "cell_phone": "+966-50-000-1002",
        "primary_fund": "Energy",
        "contact_type": "Government",
        "gender": "Female",
        "country": "Saudi Arabia",
        "fly_status": "Must Fly",
        "sectors": ["Government", "Energy"],
        "notes": "Saudi MoE. Bilateral energy cooperation track.",
    },
    {
        "name": "Margaret Foster",
        "title": "Trustee",
        "company_name": "Foster Family Office",
        "email": "mfoster@fosterfo.fake",
        "cell_phone": "555-0903",
        "primary_fund": "Maritime",
        "contact_type": "LP",
        "lp_subtype": "Family Office",
        "gender": "Female",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["LP / Capital", "Maritime"],
        "notes": "Multi-generational shipping family. $250M alts allocation.",
    },
    {
        "name": "Patricia Doyle",
        "title": "CIO",
        "company_name": "Trinity Endowment",
        "email": "pdoyle@trinityend.fake",
        "cell_phone": "555-0904",
        "primary_fund": "Critical Minerals",
        "contact_type": "LP",
        "lp_subtype": "Endowment",
        "gender": "Female",
        "country": "United States",
        "fly_status": "Fly List",
        "sectors": ["LP / Capital", "Critical Minerals"],
        "notes": "University endowment. Real assets sleeve.",
    },
]


# Per-contact ownership assignment (Phase 2 Slice 5 follow-up). Reflects
# each teammate's real role so the demo behaves like the production team:
#   - Alex Rivera (admin, AI Tools and Fund Partner): a small headline set —
#     Marcus and Captain Maria Santos.
#   - Sam Chen (Strategy and Industry Lead): the non-mineral Portfolio
#     operating companies (Maritime + Energy).
#   - Jordan Blake (Investor and Government Relations Lead): EVERY
#     Critical Minerals contact (Jordan Blake's domain), all remaining
#     LPs, all Government contacts, and the general intermediary.
# Any contact name not in this map falls back to Alex Rivera.
CONTACT_OWNER: dict[str, str] = {
    # Alex Rivera
    "Marcus Sterling": "alex@example.com",
    "Captain Maria Santos (Ret.)": "alex@example.com",
    # Sam Chen — non-mineral Portfolio companies (Maritime + Energy)
    "Admiral William Barrett (Ret.)": "sam@example.com",
    "Christine O'Neill": "sam@example.com",
    "Kevin Park": "sam@example.com",
    "Raj Patel": "sam@example.com",
    # Jordan Blake — all Critical Minerals (her domain) + remaining LPs + Government
    "Diana Cho": "jordan@example.com",
    "Sarah Kowalski": "jordan@example.com",
    "James Blackwood": "jordan@example.com",
    "Elena Rodriguez": "jordan@example.com",
    "Lisa Montenegro": "jordan@example.com",
    "Dr. Jonathan Minerals": "jordan@example.com",
    "Patricia Doyle": "jordan@example.com",
    "Sheikh Abdullah Al-Rashid": "jordan@example.com",
    "Dr. Yuki Tanaka": "jordan@example.com",
    "Khalid bin Saud": "jordan@example.com",
    "Margaret Foster": "jordan@example.com",
    "Douglas Otto": "jordan@example.com",
    "Brigadier General Helen Marsh": "jordan@example.com",
    "Robert Hayes": "jordan@example.com",
    "Dr. Layla Al-Otaibi": "jordan@example.com",
    "Anthony Chen": "jordan@example.com",
}

# Seeded by Alembic migration 9a0665bac0e7, but recreate defensively in
# case someone runs the seed against a fresh dev DB before migrations.
LCC_TEAMMATES: list[dict[str, str]] = [
    {"email": "alex@example.com", "name": "Alex Rivera", "role": "admin"},
    {"email": "sam@example.com", "name": "Sam Chen", "role": "member"},
    {"email": "jordan@example.com", "name": "Jordan Blake", "role": "member"},
]


def _get_or_create_din_users(db) -> dict[str, User]:
    """Return {email: User} for the three DIN teammates, creating any
    that don't already exist."""
    by_email: dict[str, User] = {}
    for spec in LCC_TEAMMATES:
        user = db.query(User).filter(User.email == spec["email"]).first()
        if user is None:
            user = User(
                email=spec["email"],
                name=spec["name"],
                role=spec["role"],
                intro_seen=False,
            )
            db.add(user)
            db.flush()
        by_email[spec["email"]] = user
    return by_email


def seed(reset: bool = False) -> None:
    """Seed dummy contacts. Refuses to run if ENTERPRISE_MODE=true.

    With reset=True, wipes existing contacts first — convenient when the
    schema or seed values changed.
    """
    settings = get_settings()
    if settings.enterprise_mode:
        print("ERROR: Refusing to seed dummy data with ENTERPRISE_MODE=true.")
        sys.exit(1)

    with SessionLocal() as db:
        users_by_email = _get_or_create_din_users(db)
        default_owner = users_by_email["alex@example.com"]

        if reset:
            wiped = db.query(Contact).delete()
            db.commit()
            print(f"Reset: deleted {wiped} existing contacts.")

        existing = db.query(Contact).count()
        if existing > 0:
            print(f"Database already has {existing} contacts. Skipping seed.")
            print("Pass --reset to wipe and re-seed.")
            return

        contacts: list[Contact] = []
        for payload in DUMMY_CONTACTS:
            owner_email = CONTACT_OWNER.get(str(payload["name"]), default_owner.email)
            owner = users_by_email.get(owner_email, default_owner)
            contacts.append(Contact(owner_id=owner.id, **payload))
        db.add_all(contacts)
        db.commit()
        by_owner: dict[str, int] = {}
        for c in contacts:
            owner_email = next(
                (e for e, u in users_by_email.items() if u.id == c.owner_id),
                "unknown",
            )
            by_owner[owner_email] = by_owner.get(owner_email, 0) + 1
        print(f"Seeded {len(contacts)} dummy contacts:")
        for email, count in sorted(by_owner.items()):
            print(f"  {count:>3} -> {email}")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    seed(reset=reset)
