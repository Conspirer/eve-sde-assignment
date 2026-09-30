"""Fill the database with sample data. Safe to run many times.

    python -m app.seed
"""
from decimal import Decimal

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest, User
from app.security import hash_password

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin12345"

TESTS = {
    "Complete Blood Count": "Measures red cells, white cells and platelets.",
    "Lipid Profile": "Cholesterol and triglycerides.",
    "Thyroid Profile": "T3, T4 and TSH levels.",
}

CENTRES = [
    ("EVE Diagnostics Andheri", "Mumbai", "12 Link Road, Andheri West",
     {"Complete Blood Count": "399.00", "Lipid Profile": "799.00"}),
    ("EVE Diagnostics Indiranagar", "Bengaluru", "100 Feet Road, Indiranagar",
     {"Complete Blood Count": "349.00", "Thyroid Profile": "599.00"}),
]


def seed() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == ADMIN_EMAIL)) is None:
            db.add(User(email=ADMIN_EMAIL, full_name="Admin",
                        hashed_password=hash_password(ADMIN_PASSWORD), is_admin=True))

        tests = {}
        for name, description in TESTS.items():
            test = db.scalar(select(DiagnosticTest).where(DiagnosticTest.name == name))
            if test is None:
                test = DiagnosticTest(name=name, description=description)
                db.add(test)
            tests[name] = test

        for name, city, address, prices in CENTRES:
            if db.scalar(select(DiagnosticCentre).where(DiagnosticCentre.name == name)):
                continue
            centre = DiagnosticCentre(name=name, city=city, address=address)
            centre.tests = [CentreTest(test=tests[t], price=Decimal(p)) for t, p in prices.items()]
            db.add(centre)

        db.commit()
    print(f"Seed done. Admin login: {ADMIN_EMAIL} / {ADMIN_PASSWORD}")


if __name__ == "__main__":
    seed()
