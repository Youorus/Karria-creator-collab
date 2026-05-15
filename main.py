import os
import csv
import time
from datetime import datetime
from dotenv import load_dotenv

from sqlalchemy import create_engine, text

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

from data.contants import URL, DATABASE_URL

# ======================
# ENV
# ======================

load_dotenv()



if not DATABASE_URL:
    raise ValueError("DATABASE_URL manquante")

# ======================
# DB
# ======================

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# ======================
# PATHS
# ======================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

CSV_PATH = os.path.join(DATA_DIR, "client_anef.csv") 
# ======================
# SELENIUM CONFIG
# ======================

def create_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    return webdriver.Chrome(options=options)

# ======================
# SCRAPE DATE ANEF
# ======================

def get_last_notification_date(username, password, url):
    driver = create_driver()
    wait = WebDriverWait(driver, 40)

    try:
        driver.get(url)

        wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(username)
        wait.until(EC.presence_of_element_located((By.NAME, "password"))).send_keys(password)

        wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))).click()

        time.sleep(6)

        bell = wait.until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//a[.//span[contains(@class,'fa-bell')]]"
            ))
        )
        bell.click()

        wait.until(EC.visibility_of_element_located((By.ID, "NotificationSubMenu")))

        link = wait.until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//div[@id='NotificationSubMenu']//a[contains(., 'Voir toutes mes notifications')]"
            ))
        )

        driver.execute_script("arguments[0].click();", link)

        wait.until(
            EC.presence_of_element_located((
                By.XPATH,
                "//h1 | //h2 | //h3[contains(., 'Notification')]"
            ))
        )

        soup = BeautifulSoup(driver.page_source, "html.parser")

        rows = soup.find_all("tr", class_="ng-star-inserted")

        dates = []

        for row in rows:
            span = row.find("span", class_="ui-msg-date-read")
            if not span:
                continue

            date_str = span.get_text(strip=True)

            try:
                dates.append(datetime.strptime(date_str, "%d/%m/%Y").date())
            except ValueError:
                pass

        if dates:
            return max(dates)

        return None

    finally:
        driver.quit()

# ======================
# MAIN SYNC
# ======================

def run():
    print("🔄 Synchronisation ANEF → DB")

    rows = []

    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    updated = 0

    for row in rows:
        email = row["Email ANEF"]
        password = row["Mot de passe ANEF"]
        client_id = row["Client ID"]

        if not email or not password:
            continue

        print(f"🔍 Vérification ANEF → client {client_id}")

        last_date = get_last_notification_date(email, password, URL)

        if not last_date:
            print("   ⏭ aucune notification")
            continue

        db_date = row.get("Dernière notification ANEF")

        if db_date:
            db_date = datetime.strptime(db_date, "%Y-%m-%d").date()

        if db_date == last_date:
            print("   ✅ déjà à jour")
            continue

        # ======================
        # UPDATE DB
        # ======================

        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE clients_client
                    SET last_anef_notification_date = :date
                    WHERE id = :client_id
                """),
                {
                    "date": last_date,
                    "client_id": client_id
                }
            )

        # ======================
        # UPDATE CSV
        # ======================

        row["Dernière notification ANEF"] = str(last_date)
        updated += 1

        print(f"   🔔 mise à jour → {last_date}")

    # ======================
    # SAVE CSV
    # ======================

    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ Synchronisation terminée — {updated} client(s) mis à jour")


if __name__ == "__main__":
    run()
