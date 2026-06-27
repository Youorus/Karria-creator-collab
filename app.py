"""
TikTok Influencer Scraper — Interface graphique (Streamlit)
===========================================================

Interface web simple et intuitive pour piloter le scraper sans toucher au code.

Lancement :
    pip install -r requirements.txt
    streamlit run app.py

Le navigateur s'ouvre automatiquement. On choisit le pays, la ville, les niches,
la fourchette de followers et le nombre de profils visés, puis on lance.
À la fin, on télécharge le CSV des influenceurs trouvés.
"""

import logging
import threading
from datetime import datetime
from pathlib import Path

import streamlit as st

from tiktok_scraper import (
    Config,
    TikTokScraper,
    charger_pays,
    CATEGORIES_DISPONIBLES,
    CATEGORIES_KEYWORDS,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG PAGE
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="TikTok Influencer Scraper",
    page_icon="🎯",
    layout="wide",
)

COUNTRIES = charger_pays("countries.yaml")
NICHES = [c for c in CATEGORIES_DISPONIBLES if c != "__perso__"]


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER — pilote le scraper dans un thread de fond
# ─────────────────────────────────────────────────────────────────────────────
class _ListLogHandler(logging.Handler):
    """Renvoie chaque ligne de log du scraper vers une liste partagée."""

    def __init__(self, sink: list):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        try:
            self.sink.append(self.format(record))
            # On borne la taille pour ne pas exploser la mémoire
            if len(self.sink) > 800:
                del self.sink[:-800]
        except Exception:
            pass


class ScraperRunner:
    """Encapsule un run du scraper : état partagé thread ↔ interface."""

    def __init__(self, config: Config):
        self.config = config
        self.logs: list[str] = []
        self.profils: list[dict] = []
        self.count = 0
        self.target = config.max_profils
        self.done = False
        self.error: str | None = None
        self.csv_path = config.output_csv
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    # appelé par le scraper à chaque profil validé
    def _on_progress(self, count: int, target: int, profil: dict):
        self.count = count
        self.target = target
        self.profils.append(profil)

    def _run(self):
        handler = _ListLogHandler(self.logs)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            scraper = TikTokScraper(
                self.config,
                COUNTRIES,
                progress_callback=self._on_progress,
                stop_event=self.stop_event,
            )
            self.csv_path = scraper.config.output_csv
            self.target = scraper.config.max_profils
            scraper.run()
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            logging.getLogger().error(self.error)
        finally:
            self.done = True
            root.removeHandler(handler)

    def start(self):
        self.thread.start()

    @property
    def running(self) -> bool:
        return self.thread.is_alive()


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR — paramètres
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.title("🎯 Paramètres")

runner: ScraperRunner | None = st.session_state.get("runner")
run_actif = runner is not None and runner.running

# On verrouille le formulaire pendant un run
disabled = run_actif

pays_codes = list(COUNTRIES.keys())
pays = st.sidebar.selectbox(
    "Pays (définit la langue & la locale)",
    pays_codes,
    format_func=lambda c: f"{c} — {COUNTRIES[c].get('tiktok_locale', '?')}",
    disabled=disabled,
)

ville = st.sidebar.text_input(
    "Ville cible (optionnel)",
    placeholder="ex : Paris",
    help="Laisser vide pour cibler le pays entier. Sinon la recherche se concentre sur cette ville.",
    disabled=disabled,
)

st.sidebar.markdown("**Niches**")
niches = st.sidebar.multiselect(
    "Catégories",
    NICHES,
    help="Laisser vide = filet large (toutes niches). Sinon seuls les profils correspondant sont gardés.",
    disabled=disabled,
)
niche_libre = st.sidebar.text_input(
    "Niche personnalisée (optionnel)",
    placeholder="ex : crypto memecoin",
    help="Une niche libre non listée. Ses mots seront utilisés pour la recherche et le filtrage.",
    disabled=disabled,
)

st.sidebar.markdown("**Followers**")
illimite = st.sidebar.checkbox("Pas de maximum (illimité)", value=False, disabled=disabled)
fmin, fmax = st.sidebar.slider(
    "Fourchette de followers",
    min_value=0,
    max_value=2_000_000,
    value=(10_000, 200_000),
    step=1_000,
    disabled=disabled,
)
if illimite:
    fmax = 0
    st.sidebar.caption(f"Minimum {fmin:,} followers — maximum illimité")
else:
    st.sidebar.caption(f"De {fmin:,} à {fmax:,} followers")

max_profils = st.sidebar.number_input(
    "Nombre de profils visés",
    min_value=1,
    max_value=5_000,
    value=100,
    step=10,
    disabled=disabled,
)

with st.sidebar.expander("⚙️ Options avancées"):
    from_hashtags = st.checkbox("Collecter depuis les hashtags", value=True, disabled=disabled)
    from_search = st.checkbox("Collecter depuis la recherche vidéo", value=True, disabled=disabled)
    min_score = st.slider(
        "Score d'appartenance minimum",
        0, 15, 1,
        help="Plus haut = profils plus sûrement du pays/ville, mais moins de volume.",
        disabled=disabled,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CORPS PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
st.title("TikTok Influencer Scraper")
st.caption(
    "Trouve des influenceurs par pays, ville, niche et nombre de followers — "
    "puis exporte le CSV pour les contacter."
)

col_a, col_b = st.columns([1, 1])

with col_a:
    lancer = st.button(
        "▶ Lancer le scraping",
        type="primary",
        disabled=disabled,
        use_container_width=True,
    )

with col_b:
    if run_actif:
        if st.button("⏹ Arrêter", use_container_width=True):
            runner.stop_event.set()
            st.warning("Arrêt demandé — le scraper termine le profil en cours puis sauvegarde…")
    elif runner is not None:
        if st.button("🔄 Nouveau scraping", use_container_width=True):
            st.session_state.runner = None
            st.rerun()


# ── Démarrage d'un run ────────────────────────────────────────────────────────
if lancer and not run_actif:
    if not from_hashtags and not from_search:
        st.error("Active au moins une source (hashtags ou recherche vidéo) dans les options avancées.")
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        config = Config(
            pays_code=pays,
            ville=ville.strip(),
            niche_libre=niche_libre.strip(),
            categories=list(niches),
            min_followers=int(fmin),
            max_followers=int(fmax),
            max_profils=int(max_profils),
            min_country_score=int(min_score),
            collect_from_hashtags=bool(from_hashtags),
            collect_from_search=bool(from_search),
        )
        new_runner = ScraperRunner(config)
        new_runner.start()
        st.session_state.runner = new_runner
        st.rerun()


# ── Affichage de l'état du run ────────────────────────────────────────────────
runner = st.session_state.get("runner")

if runner is not None:
    st.divider()

    target = max(runner.target, 1)
    pct = min(runner.count / target, 1.0)

    if runner.running:
        st.progress(pct, text=f"🔎 Collecte en cours — {runner.count}/{runner.target} profils trouvés")
    elif runner.error:
        st.error(f"❌ Erreur : {runner.error}")
    elif runner.stop_event.is_set():
        st.info(f"⏹ Arrêté — {runner.count} profils collectés et sauvegardés.")
    else:
        st.success(f"✅ Terminé — {runner.count} profils collectés.")
        st.progress(1.0)

    # Métriques rapides
    m1, m2, m3 = st.columns(3)
    m1.metric("Profils trouvés", runner.count)
    m2.metric("Objectif", runner.target)
    if runner.profils:
        moy = sum(p.get("followers", 0) for p in runner.profils) / len(runner.profils)
        m3.metric("Followers moyen", f"{int(moy):,}")

    # Tableau des profils
    if runner.profils:
        st.subheader("Influenceurs collectés")
        colonnes = ["pseudo", "nom", "followers", "engagement", "categories", "region", "url"]
        table = [{c: p.get(c, "") for c in colonnes} for p in runner.profils]
        st.dataframe(table, use_container_width=True, hide_index=True)

    # Téléchargement CSV
    csv_file = Path(runner.csv_path) if runner.csv_path else None
    if csv_file and csv_file.exists():
        st.download_button(
            "⬇ Télécharger le CSV",
            data=csv_file.read_bytes(),
            file_name=csv_file.name,
            mime="text/csv",
            type="primary",
            disabled=runner.running,
            use_container_width=True,
        )

    # Journal d'exécution
    with st.expander("📋 Journal d'exécution", expanded=runner.running):
        st.code("\n".join(runner.logs[-200:]) or "En attente…", language="text")

    # Rafraîchissement auto pendant le run
    if runner.running:
        import time
        time.sleep(1.5)
        st.rerun()
else:
    st.info("👈 Configure tes paramètres dans le panneau de gauche, puis clique sur **Lancer le scraping**.")
