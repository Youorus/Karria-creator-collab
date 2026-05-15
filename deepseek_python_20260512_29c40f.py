"""
TikTok Influencer Scraper — Côte d'Ivoire Video First
=====================================================

Logique :
1. Générer des recherches locales Côte d'Ivoire :
   - hashtags : #cotedivoire, #abidjan, #225, #babi...
   - villes : Abidjan, Yopougon, Cocody, Bouaké...
   - catégories : cuisine, beauté, humour, fitness...
2. Parcourir les pages vidéos TikTok.
3. Extraire les URLs vidéos.
4. Depuis les vidéos, récupérer les créateurs.
5. Enrichir les profils.
6. Filtrer :
   - score Côte d'Ivoire
   - langue fr
   - followers min/max
   - catégories
7. Export CSV.
"""

import csv
import re
import json
import time
import random
import logging
import argparse
import hashlib
import unicodedata
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import Optional
from urllib.parse import quote_plus

import requests
import yaml
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# OUTILS TEXTE
# ─────────────────────────────────────────────
def normalize_text(value: str) -> str:
    if not value:
        return ""
    value = value.lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    value = value.replace("’", "'")
    return value


def parse_count(texte: str) -> int:
    if not texte:
        return 0

    texte = normalize_text(texte)
    texte = re.sub(r"[\s,\u202f\xa0]", "", texte)

    m = re.match(r"([\d.]+)([km]?)", texte)
    if not m:
        return 0

    nb = float(m.group(1))

    if m.group(2) == "k":
        nb *= 1_000
    elif m.group(2) == "m":
        nb *= 1_000_000

    return int(nb)


def delai_aleatoire(minimum: float = 3.0, maximum: float = 8.0) -> None:
    time.sleep(random.uniform(minimum, maximum))


def fingerprint_url(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def url_vers_username(url: str) -> str:
    match = re.search(r"tiktok\.com/@([^/?#]+)", url)
    if match:
        return match.group(1).strip()
    return url.rstrip("/").split("/")[-1].lstrip("@")


def username_depuis_video_url(url: str) -> Optional[str]:
    match = re.search(r"tiktok\.com/@([^/?#]+)/video/", url)
    if match:
        return match.group(1).strip()
    return None


# ─────────────────────────────────────────────
# CIBLAGE CÔTE D’IVOIRE
# ─────────────────────────────────────────────
COUNTRY_TARGETS = {
    "CI": {
        "langue_code": "fr",
        "tiktok_locale": "fr-CI",
        "datacenter_cookie": "fra02",

        "hashtags": [
            "cotedivoire",
            "coteivoire",
            "cotedivoire🇨🇮",
            "abidjan",
            "abidjan225",
            "225",
            "babi",
            "ivoirien",
            "ivoirienne",
            "ivoiriens",
            "yopougon",
            "cocody",
            "marcory",
            "plateau",
            "treichville",
            "koumassi",
            "bouake",
            "bassam",
            "sanpedro",
            "korhogo",
            "humourivoirien",
            "comedieivoirienne",
            "buzzivoire",
            "tiktokci",
            "tiktokcotedivoire",
            "businessabidjan",
            "modeivoirienne",
            "cuisineivoirienne",
            "garba",
            "attiéké",
            "attieke",
            "alloco",
            "foutou",
        ],

        "local_keywords": [
            "côte d'ivoire",
            "cote d ivoire",
            "cotedivoire",
            "abidjan",
            "225",
            "babi",
            "ivoirien",
            "ivoirienne",
            "yopougon",
            "cocody",
            "bouaké",
            "bouake",
            "marcory",
            "plateau",
            "treichville",
            "koumassi",
            "bassam",
            "san pedro",
            "korhogo",
            "abobo",
            "riviera",
            "bingerville",
            "dabou",
            "ci",
        ],

        "strong_signals": [
            "cote d ivoire",
            "côte d'ivoire",
            "cotedivoire",
            "abidjan",
            "225",
            "yopougon",
            "cocody",
            "babi",
            "ivoirien",
            "ivoirienne",
        ],
    }
}


# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
@dataclass
class Config:
    pays_code: str = "CI"
    langue_code: str = "fr"
    tiktok_locale: str = "fr-CI"
    datacenter_cookie: str = "fra02"

    min_followers: int = 5_000
    max_followers: int = 200_000

    categories: list[str] = field(default_factory=list)
    mots_cles: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)

    max_profils: int = 100
    max_videos_par_recherche: int = 120
    max_scrolls_par_recherche: int = 18
    stop_apres_scrolls_sans_nouveau: int = 5

    min_country_score: int = 2

    output_csv: str = "influenceurs_ci.csv"
    checkpoint_file: str = "checkpoint_ci.json"

    delai_min: float = 4.0
    delai_max: float = 9.0
    scroll_pause: float = 3.0
    max_retries: int = 3

    collect_from_hashtags: bool = True
    collect_from_search: bool = False

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "Config":
        c = cls()

        if args.pays:
            c.pays_code = args.pays.upper()
        if args.langue:
            c.langue_code = args.langue
        if args.locale:
            c.tiktok_locale = args.locale
        if args.min is not None:
            c.min_followers = args.min
        if args.max is not None:
            c.max_followers = args.max
        if args.categories:
            c.categories = args.categories
        if args.mots_cles:
            c.mots_cles = args.mots_cles
        if args.hashtags:
            c.hashtags = args.hashtags
        if args.output:
            c.output_csv = args.output
        if args.max_profils:
            c.max_profils = args.max_profils

        return c

    def apply_country_defaults(self) -> None:
        target = COUNTRY_TARGETS.get(self.pays_code)
        if not target:
            return

        self.langue_code = self.langue_code or target["langue_code"]
        self.tiktok_locale = self.tiktok_locale or target["tiktok_locale"]
        self.datacenter_cookie = self.datacenter_cookie or target["datacenter_cookie"]

        if not self.hashtags:
            self.hashtags = target["hashtags"]


# ─────────────────────────────────────────────
# CATÉGORIES
# ─────────────────────────────────────────────
CATEGORIES_KEYWORDS: dict[str, list[str]] = {
    "fitness": ["fitness", "musculation", "workout", "entrainement", "gym"],
    "yoga": ["yoga", "meditation", "bien etre"],
    "cuisine": ["recette", "cuisine", "food", "gastronomie", "chef", "garba", "attieke", "alloco"],
    "voyage": ["voyage", "travel", "aventure", "vacances"],
    "mode": ["mode", "fashion", "outfit", "style", "tendance"],
    "beaute": ["beaute", "maquillage", "skincare", "makeup", "cosmetique"],
    "famille": ["famille", "parentalite", "bebe", "enfants", "maman"],
    "gaming": ["gaming", "gamer", "jeu video", "esport", "stream"],
    "tech": ["tech", "technologie", "smartphone", "ia", "informatique"],
    "coding": ["programmation", "developpeur", "code", "python", "dev"],
    "finance": ["investissement", "crypto", "bourse", "finance", "trading"],
    "business": ["entrepreneur", "startup", "marketing", "business"],
    "musique": ["musique", "chanteur", "beatmaker", "rap", "cover"],
    "art": ["art", "dessin", "peinture", "illustration", "creatif"],
    "cinema": ["cinema", "film", "serie", "critique", "acteur"],
    "comedie": ["humour", "sketch", "comedy", "blague", "drole", "comedie"],
    "education": ["apprendre", "cours", "astuce", "conseil", "tuto"],
    "developpement": ["developpement personnel", "motivation", "mindset"],
    "sport": ["sport", "football", "basket", "tennis", "athlete"],
    "nature": ["nature", "animaux", "ecologie", "jardinage"],
    "general": ["vlog", "viral", "storytime", "journee"],
}


def detecter_categories(bio: str, categories_cibles: list[str]) -> list[str]:
    bio_norm = normalize_text(bio)
    detectees = []

    for cat in categories_cibles:
        keywords = CATEGORIES_KEYWORDS.get(cat, [cat])
        if any(normalize_text(kw) in bio_norm for kw in keywords):
            detectees.append(cat)

    return detectees


# ─────────────────────────────────────────────
# MODÈLE
# ─────────────────────────────────────────────
@dataclass
class Influenceur:
    date_collecte: str
    nom: str
    pseudo: str
    followers: int
    followers_raw: str
    likes: int
    likes_raw: str
    videos: int
    langue: str
    region: str
    country_score: int
    categories_detectees: str
    bio: str
    url: str
    source: str
    source_videos: str
    score_engagement: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class CreatorCandidate:
    username: str
    profile_url: str
    source_terms: set[str] = field(default_factory=set)
    video_urls: set[str] = field(default_factory=set)

    def add_source(self, term: str, video_url: Optional[str] = None) -> None:
        if term:
            self.source_terms.add(term)
        if video_url:
            self.video_urls.add(video_url)


# ─────────────────────────────────────────────
# CHECKPOINT
# ─────────────────────────────────────────────
class Checkpoint:
    def __init__(self, path: str):
        self.path = Path(path)
        self.data: dict = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        return {"traites": [], "valides": 0}

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def deja_traite(self, url: str) -> bool:
        return fingerprint_url(url) in self.data["traites"]

    def marquer(self, url: str, valide: bool = False) -> None:
        fp = fingerprint_url(url)
        if fp not in self.data["traites"]:
            self.data["traites"].append(fp)
        if valide:
            self.data["valides"] += 1
        self.save()

    @property
    def nb_valides(self) -> int:
        return self.data["valides"]


# ─────────────────────────────────────────────
# DRIVER
# ─────────────────────────────────────────────
class DriverFactory:
    @staticmethod
    def creer(config: Config) -> webdriver.Chrome:
        opts = webdriver.ChromeOptions()
        opts.add_argument(f"--lang={config.tiktok_locale}")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--window-size=1440,900")

        opts.add_experimental_option("prefs", {
            "intl.accept_languages": f"{config.tiktok_locale},{config.langue_code};q=0.9",
            "profile.default_content_setting_values.geolocation": 1,
        })

        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=opts,
        )
        return driver


# ─────────────────────────────────────────────
# EXTRACTION PROFIL
# ─────────────────────────────────────────────
class ProfileExtractor:
    HEADERS_TEMPLATE = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "{locale},fr;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.tiktok.com/",
    }

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            k: v.format(locale=config.tiktok_locale)
            for k, v in self.HEADERS_TEMPLATE.items()
        })

    def extraire_metadata(self, username: str) -> dict:
        url = f"https://www.tiktok.com/@{username}"

        for tentative in range(self.config.max_retries):
            try:
                r = self.session.get(url, timeout=15)

                if r.status_code == 429:
                    log.warning("Rate limit 429 — pause")
                    time.sleep(30)
                    continue

                if r.status_code != 200:
                    log.warning(f"HTTP {r.status_code} pour @{username}")
                    return {}

                soup = BeautifulSoup(r.text, "html.parser")

                data = self._extraire_json_universel(soup)
                if data:
                    return data

                data = self._extraire_next_data(soup)
                if data:
                    return data

                return {}

            except requests.RequestException as e:
                log.warning(f"Tentative {tentative + 1}/{self.config.max_retries} échouée : {e}")
                time.sleep(5 * (tentative + 1))

        return {}

    def _extraire_json_universel(self, soup: BeautifulSoup) -> dict:
        script = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
        if not script or not script.string:
            return {}

        try:
            raw = json.loads(script.string)
            user_detail = (
                raw
                .get("__DEFAULT_SCOPE__", {})
                .get("webapp.user-detail", {})
                .get("userInfo", {})
            )

            user = user_detail.get("user", {})
            stats = user_detail.get("stats", {})

            return {
                "langue": user.get("language", ""),
                "region": user.get("region", ""),
                "bio": user.get("signature", ""),
                "verified": user.get("verified", False),
                "followers_api": stats.get("followerCount", 0),
                "likes_api": stats.get("heartCount", 0),
                "videos_api": stats.get("videoCount", 0),
            }

        except Exception:
            return {}

    def _extraire_next_data(self, soup: BeautifulSoup) -> dict:
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            return {}

        try:
            raw = json.loads(script.string)
            user_info = (
                raw
                .get("props", {})
                .get("pageProps", {})
                .get("userInfo", {})
            )

            user = user_info.get("user", {})
            stats = user_info.get("stats", {})

            return {
                "langue": user.get("language", ""),
                "region": user.get("region", ""),
                "bio": user.get("signature", ""),
                "verified": user.get("verified", False),
                "followers_api": stats.get("followerCount", 0),
                "likes_api": stats.get("heartCount", 0),
                "videos_api": stats.get("videoCount", 0),
            }

        except Exception:
            return {}


# ─────────────────────────────────────────────
# COLLECTEUR VIDÉOS → CRÉATEURS
# ─────────────────────────────────────────────
class UrlCollector:
    def __init__(self, driver: webdriver.Chrome, config: Config):
        self.driver = driver
        self.config = config

    def initialiser(self) -> None:
        home = f"https://www.tiktok.com/{self.config.langue_code}/"
        log.info(f"Ouverture TikTok : {home}")
        self.driver.get(home)
        time.sleep(4)
        self._injecter_cookies_localisation()
        self.driver.refresh()
        time.sleep(3)

    def _injecter_cookies_localisation(self) -> None:
        cookies = {
            "tt-target-idc": self.config.datacenter_cookie,
            "app_language": self.config.langue_code,
            "webapp_language": self.config.langue_code,
        }

        for name, value in cookies.items():
            try:
                self.driver.add_cookie({"name": name, "value": value})
            except Exception:
                pass

    def collecter_createurs_depuis_hashtag(self, hashtag: str) -> dict[str, CreatorCandidate]:
        url = f"https://www.tiktok.com/tag/{quote_plus(hashtag)}"
        return self._collecter_createurs_depuis_page(url, source_term=f"#{hashtag}")

    def collecter_createurs_depuis_recherche_video(self, mot_cle: str) -> dict[str, CreatorCandidate]:
        url = f"https://www.tiktok.com/search/video?q={quote_plus(mot_cle)}"
        return self._collecter_createurs_depuis_page(url, source_term=mot_cle)

    def _collecter_createurs_depuis_page(self, url: str, source_term: str) -> dict[str, CreatorCandidate]:
        log.info(f"Recherche vidéos : {source_term} → {url}")
        self.driver.get(url)
        time.sleep(5)

        candidates: dict[str, CreatorCandidate] = {}

        sans_nouveau = 0
        scroll_count = 0

        while (
            scroll_count < self.config.max_scrolls_par_recherche
            and sans_nouveau < self.config.stop_apres_scrolls_sans_nouveau
            and len(candidates) < self.config.max_videos_par_recherche
        ):
            nouveau = 0
            html = self.driver.page_source
            soup = BeautifulSoup(html, "html.parser")

            video_urls = self._extraire_video_urls(soup)
            profile_urls = self._extraire_profile_urls(soup)

            for video_url in video_urls:
                username = username_depuis_video_url(video_url)
                if not username:
                    continue

                profile_url = f"https://www.tiktok.com/@{username}"

                if username not in candidates:
                    candidates[username] = CreatorCandidate(
                        username=username,
                        profile_url=profile_url,
                    )
                    nouveau += 1

                candidates[username].add_source(source_term, video_url)

            for profile_url in profile_urls:
                username = url_vers_username(profile_url)
                if not username:
                    continue

                if username not in candidates:
                    candidates[username] = CreatorCandidate(
                        username=username,
                        profile_url=profile_url,
                    )
                    nouveau += 1

                candidates[username].add_source(source_term)

            log.info(
                f"  Scroll {scroll_count + 1}/{self.config.max_scrolls_par_recherche} "
                f"— créateurs={len(candidates)} (+{nouveau})"
            )

            sans_nouveau = 0 if nouveau else sans_nouveau + 1

            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(self.config.scroll_pause, self.config.scroll_pause + 2.5))
            scroll_count += 1

        return candidates

    def _extraire_video_urls(self, soup: BeautifulSoup) -> set[str]:
        urls = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]

            if "/video/" not in href:
                continue

            if href.startswith("/"):
                href = "https://www.tiktok.com" + href

            href = href.split("?")[0]

            if "tiktok.com/@" in href:
                urls.add(href)

        return urls

    def _extraire_profile_urls(self, soup: BeautifulSoup) -> set[str]:
        urls = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]

            if "/@" not in href:
                continue

            if "/video/" in href:
                continue

            if href in ("/@", "/@/"):
                continue

            if href.startswith("/"):
                href = "https://www.tiktok.com" + href

            href = href.split("?")[0].rstrip("/")

            if re.search(r"tiktok\.com/@[^/?#]+$", href):
                urls.add(href)

        return urls

    def extraire_donnees_profil_selenium(
        self,
        candidate: CreatorCandidate,
        meta: dict,
        country_score: int,
    ) -> Optional[Influenceur]:
        url = candidate.profile_url

        try:
            self.driver.get(url)
            time.sleep(3)
        except Exception:
            log.warning(f"Profil inaccessible : {url}")
            return None

        soup = BeautifulSoup(self.driver.page_source, "html.parser")

        def txt(selector: str) -> str:
            el = soup.find(attrs={"data-e2e": selector})
            if not el:
                return ""
            return el.get_text(strip=True)

        nom = txt("user-title") or candidate.username
        pseudo = txt("user-subtitle").lstrip("@") or candidate.username

        followers_raw = txt("followers-count")
        likes_raw = txt("likes-count")
        videos_raw = txt("video-count")

        followers = meta.get("followers_api") or parse_count(followers_raw)
        likes = meta.get("likes_api") or parse_count(likes_raw)
        videos = meta.get("videos_api") or parse_count(videos_raw)
        bio = meta.get("bio", "")

        score_engagement = round(likes / followers, 4) if followers > 0 else 0.0
        cats = detecter_categories(bio, self.config.categories)

        return Influenceur(
            date_collecte=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            nom=nom,
            pseudo=pseudo,
            followers=followers,
            followers_raw=followers_raw or str(followers),
            likes=likes,
            likes_raw=likes_raw or str(likes),
            videos=videos,
            langue=meta.get("langue", ""),
            region=meta.get("region", ""),
            country_score=country_score,
            categories_detectees=",".join(cats),
            bio=bio,
            url=url,
            source=",".join(sorted(candidate.source_terms)),
            source_videos=" | ".join(sorted(candidate.video_urls)[:10]),
            score_engagement=score_engagement,
        )


# ─────────────────────────────────────────────
# SCRAPER PRINCIPAL
# ─────────────────────────────────────────────
class TikTokScraper:
    def __init__(self, config: Config):
        self.config = config
        self.config.apply_country_defaults()

        self.checkpoint = Checkpoint(config.checkpoint_file)
        self.extractor = ProfileExtractor(config)

        self.driver: Optional[webdriver.Chrome] = None
        self.collector: Optional[UrlCollector] = None

        if not self.config.mots_cles:
            self.config.mots_cles = self._generer_mots_cles()

        log.info(f"Pays : {self.config.pays_code}")
        log.info(f"Locale : {self.config.tiktok_locale}")
        log.info(f"Followers : {self.config.min_followers} - {self.config.max_followers}")
        log.info(f"Score pays minimum : {self.config.min_country_score}")
        log.info(f"Catégories : {self.config.categories or 'toutes'}")
        log.info(f"Hashtags : {self.config.hashtags[:15]}...")
        log.info(f"Mots-clés : {self.config.mots_cles[:15]}...")

    def _generer_mots_cles(self) -> list[str]:
        target = COUNTRY_TARGETS.get(self.config.pays_code, {})
        local_keywords = target.get("local_keywords", [])

        mots = []

        if self.config.categories:
            for cat in self.config.categories:
                cat_keywords = CATEGORIES_KEYWORDS.get(cat, [cat])

                for local in local_keywords[:12]:
                    for ck in cat_keywords[:3]:
                        mots.append(f"{ck} {local}")
                        mots.append(f"{local} {ck}")
        else:
            mots.extend(local_keywords)
            mots.extend([
                "humour ivoirien",
                "cuisine ivoirienne",
                "mode ivoirienne",
                "business abidjan",
                "abidjan vlog",
                "babi humour",
                "cote d ivoire tiktok",
            ])

        return list(dict.fromkeys(mots))

    def compute_country_score(self, candidate: CreatorCandidate, meta: dict) -> int:
        target = COUNTRY_TARGETS.get(self.config.pays_code, {})
        strong = [normalize_text(x) for x in target.get("strong_signals", [])]
        local = [normalize_text(x) for x in target.get("local_keywords", [])]

        text = normalize_text(
            " ".join([
                candidate.username,
                " ".join(candidate.source_terms),
                " ".join(candidate.video_urls),
                meta.get("bio", ""),
                meta.get("region", ""),
                meta.get("langue", ""),
            ])
        )

        score = 0

        region = meta.get("region", "")
        langue = meta.get("langue", "")

        if region == self.config.pays_code:
            score += 5

        if langue == self.config.langue_code:
            score += 1

        for signal in strong:
            if signal and signal in text:
                score += 3

        for signal in local:
            if signal and signal in text:
                score += 1

        if any(term.startswith("#") for term in candidate.source_terms):
            score += 1

        if len(candidate.video_urls) >= 2:
            score += 1

        return score

    def _est_valide(self, inf: Influenceur) -> tuple[bool, str]:
        cfg = self.config

        if inf.country_score < cfg.min_country_score:
            return False, f"Score CI insuffisant ({inf.country_score})"

        if inf.region and inf.region != cfg.pays_code:
            return False, f"Région={inf.region} ≠ {cfg.pays_code}"

        if inf.langue and inf.langue != cfg.langue_code:
            return False, f"Langue={inf.langue} ≠ {cfg.langue_code}"

        if inf.followers < cfg.min_followers:
            return False, f"Abonnés insuffisants ({inf.followers})"

        if cfg.max_followers > 0 and inf.followers > cfg.max_followers:
            return False, f"Trop d'abonnés ({inf.followers})"

        if cfg.categories and not inf.categories_detectees:
            return False, "Aucune catégorie cible détectée"

        return True, ""

    def run(self) -> None:
        self.driver = DriverFactory.creer(self.config)
        self.collector = UrlCollector(self.driver, self.config)

        all_candidates: dict[str, CreatorCandidate] = {}

        try:
            self.collector.initialiser()

            if self.config.collect_from_hashtags:
                for hashtag in self.config.hashtags:
                    if self.checkpoint.nb_valides >= self.config.max_profils:
                        break

                    candidates = self.collector.collecter_createurs_depuis_hashtag(hashtag)
                    self._merge_candidates(all_candidates, candidates)
                    log.info(f"Total candidats cumulés : {len(all_candidates)}")
                    delai_aleatoire(2, 5)

            if self.config.collect_from_search:
                for mot in self.config.mots_cles:
                    if self.checkpoint.nb_valides >= self.config.max_profils:
                        break

                    candidates = self.collector.collecter_createurs_depuis_recherche_video(mot)
                    self._merge_candidates(all_candidates, candidates)
                    log.info(f"Total candidats cumulés : {len(all_candidates)}")
                    delai_aleatoire(2, 5)

            log.info(f"{len(all_candidates)} créateurs candidats à analyser")

            output = Path(self.config.output_csv)
            mode = "a" if output.exists() else "w"
            headers = list(Influenceur.__dataclass_fields__.keys())

            with open(output, mode, newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)

                if mode == "w":
                    writer.writeheader()

                candidates_sorted = sorted(
                    all_candidates.values(),
                    key=lambda c: len(c.source_terms) + len(c.video_urls),
                    reverse=True,
                )

                for idx, candidate in enumerate(candidates_sorted, 1):
                    if self.checkpoint.nb_valides >= self.config.max_profils:
                        log.info(f"Limite atteinte : {self.config.max_profils}")
                        break

                    if self.checkpoint.deja_traite(candidate.profile_url):
                        continue

                    log.info(f"({idx}/{len(candidates_sorted)}) @{candidate.username}")

                    meta = self.extractor.extraire_metadata(candidate.username)

                    if not meta:
                        log.info("  ✗ métadonnées introuvables")
                        self.checkpoint.marquer(candidate.profile_url)
                        continue

                    country_score = self.compute_country_score(candidate, meta)

                    if country_score < self.config.min_country_score:
                        log.info(f"  ✗ score CI trop faible : {country_score}")
                        self.checkpoint.marquer(candidate.profile_url)
                        continue

                    inf = self.collector.extraire_donnees_profil_selenium(
                        candidate=candidate,
                        meta=meta,
                        country_score=country_score,
                    )

                    if inf is None:
                        self.checkpoint.marquer(candidate.profile_url)
                        continue

                    valide, raison = self._est_valide(inf)

                    if not valide:
                        log.info(f"  ✗ {raison}")
                        self.checkpoint.marquer(candidate.profile_url)
                        continue

                    writer.writerow(inf.as_dict())
                    f.flush()

                    self.checkpoint.marquer(candidate.profile_url, valide=True)

                    log.info(
                        f"  ✓ @{inf.pseudo} | "
                        f"{inf.followers} abonnés | "
                        f"score CI={inf.country_score} | "
                        f"cats={inf.categories_detectees or 'N/A'}"
                    )

                    delai_aleatoire(self.config.delai_min, self.config.delai_max)

        except KeyboardInterrupt:
            log.info("Interruption manuelle — progression sauvegardée.")

        except WebDriverException as e:
            log.error(f"Erreur Selenium : {e}")

        finally:
            if self.driver:
                self.driver.quit()

            log.info(
                f"Terminé : {self.checkpoint.nb_valides} profils valides → {self.config.output_csv}"
            )

    def _merge_candidates(
        self,
        base: dict[str, CreatorCandidate],
        new: dict[str, CreatorCandidate],
    ) -> None:
        for username, candidate in new.items():
            if username not in base:
                base[username] = candidate
            else:
                base[username].source_terms |= candidate.source_terms
                base[username].video_urls |= candidate.video_urls


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TikTok Influencer Scraper Côte d'Ivoire")

    p.add_argument("--config", help="Fichier YAML")
    p.add_argument("--pays", help="Code pays ISO, ex: CI")
    p.add_argument("--langue", help="Code langue, ex: fr")
    p.add_argument("--locale", help="Locale TikTok, ex: fr-CI")

    p.add_argument("--min", type=int, help="Minimum abonnés")
    p.add_argument("--max", type=int, help="Maximum abonnés, 0 = illimité")

    p.add_argument("--categories", nargs="+", help="Catégories ciblées")
    p.add_argument("--mots-cles", nargs="+", dest="mots_cles", help="Mots-clés personnalisés")
    p.add_argument("--hashtags", nargs="+", help="Hashtags personnalisés")

    p.add_argument("--output", help="CSV de sortie")
    p.add_argument("--max-profils", type=int, dest="max_profils", help="Nombre max de profils valides")

    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.config:
        config = Config.from_yaml(args.config)
    else:
        config = Config.from_args(args)

    scraper = TikTokScraper(config)
    scraper.run()