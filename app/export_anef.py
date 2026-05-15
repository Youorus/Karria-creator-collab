import os
import csv
from datetime import datetime

from sqlalchemy import create_engine, text

from data.contants import DATABASE_URL

# ======================
# LOAD ENV
# ======================



if not DATABASE_URL:
    raise ValueError("DATABASE_URL manquante")

# ======================
# DB CONNECTION
# ======================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

# ======================
# EXPORT CSV
# ======================

def run():
    print("📤 Export CSV des comptes ANEF en cours...")

    query = text("""
        SELECT DISTINCT
            l.last_name,
            l.first_name,
            c.anef_email,
            c.anef_password,
            c.last_anef_notification_date,
            c.id AS client_id,
            l.id AS lead_id
        FROM clients_client c
        JOIN contracts_contract ct
            ON ct.client_id = c.id
        JOIN leads_lead l
            ON l.id = c.lead_id
        WHERE
            c.anef_email IS NOT NULL
            OR c.anef_password IS NOT NULL
        ORDER BY l.last_name, l.first_name
    """)

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    # ======================
    # DOSSIER DATA
    # ======================

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.join(BASE_DIR, "data")

    os.makedirs(DATA_DIR, exist_ok=True)

    filename = os.path.join(
        DATA_DIR,
        f"client_anef.csv"
    )

    # ======================
    # ÉCRITURE CSV
    # ======================

    with open(filename, mode="w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)

        writer.writerow([
            "Nom",
            "Prénom",
            "Email ANEF",
            "Mot de passe ANEF",
            "Dernière notification ANEF",
            "Client ID",
            "Lead ID",
        ])

        count = 0

        for row in rows:
            writer.writerow([
                row["last_name"],
                row["first_name"],
                row["anef_email"] or "",
                row["anef_password"] or "",
                row["last_anef_notification_date"],
                row["client_id"],
                row["lead_id"],
            ])
            count += 1

    print(f"✅ Export terminé : {count} client(s)")
    print(f"📁 Fichier sauvegardé dans : {filename}")


if __name__ == "__main__":
    run()
