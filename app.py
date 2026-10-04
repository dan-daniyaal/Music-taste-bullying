"""
Roast My Aux — a Flask app that roasts your Spotify taste and your love life.
"""
import hashlib
import json
import os
import random

import requests
import spotipy
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for
from spotipy.cache_handler import FlaskSessionCacheHandler
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.urandom(24)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TIME_RANGES = {
    "short_term": "last 4 weeks",
    "medium_term": "last 6 months",
    "long_term": "all time",
}


def load_json(name):
    with open(os.path.join(BASE_DIR, "data", name), "r", encoding="utf-8") as f:
        return json.load(f)


PICKUP_LINES = load_json("pickup_lines.json")
ARTIST_ROASTS = load_json("artist_roasts.json")
GENRE_ROASTS = load_json("genre_roasts.json")
DEMO_ARTISTS = load_json("demo_artists.json")

SPOTIFY_CONFIGURED = all(
    os.getenv(k) for k in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI")
)
NOT_CONFIGURED_MSG = ("Spotify login isn't set up on this server yet "
                      "(missing keys in .env). The demo still works!")


def spotify_oauth():
    # Tokens live in the signed Flask session cookie — nothing is ever written
    # to disk, so there is no token cache file that could be committed by accident.
    return SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope="user-top-read",
        cache_handler=FlaskSessionCacheHandler(session),
        show_dialog=True,
    )


def spotify_client():
    """Return an authenticated Spotify client, refreshing the token if needed."""
    oauth = spotify_oauth()
    token = oauth.validate_token(oauth.cache_handler.get_cached_token())
    return spotipy.Spotify(auth=token["access_token"]) if token else None


# ---- Roast logic ------------------------------------------------------------

def simplify(artist):
    images = artist.get("images") or []
    return {
        "name": artist["name"],
        "genres": artist.get("genres", [])[:3],
        "popularity": artist.get("popularity", 50),
        "image": images[-2]["url"] if len(images) > 1 else (images[0]["url"] if images else None),
        "url": artist.get("external_urls", {}).get("spotify"),
    }


def build_roast(artists):
    """Artist-specific roast first, then a genre roast, then a generic fallback."""
    if not artists:
        return ("Spotify says you have no top artists. Either you're built different, "
                "or you're emotionally unavailable — musically.")
    for artist in artists:
        if artist["name"] in ARTIST_ROASTS:
            return ARTIST_ROASTS[artist["name"]]
    for artist in artists:
        for genre in artist["genres"]:
            for key, roast in GENRE_ROASTS.items():
                if key in genre:
                    return roast
    return f"Your top artist is {artists[0]['name']}. Not bad. Slightly dramatic, but we respect it."


def basic_meter(artists):
    """Average artist popularity (0–100) — how mainstream your taste is."""
    if not artists:
        return 0, "Unknown"
    score = round(sum(a["popularity"] for a in artists) / len(artists))
    if score >= 80:
        verdict = "Certified basic. Your Wrapped is the Top 50 Global playlist."
    elif score >= 60:
        verdict = "Mainstream with a side of 'I liked them before they blew up.'"
    elif score >= 40:
        verdict = "Respectably niche. You definitely mention it at parties."
    else:
        verdict = "Underground to the point of concern. Do your artists know they're artists?"
    return score, verdict


def love_score_for(artists):
    """Stable score for a given taste (same artists → same score), random otherwise."""
    if not artists:
        return random.randint(0, 100)
    seed = "|".join(a["name"] for a in artists)
    return int(hashlib.sha256(seed.encode()).hexdigest(), 16) % 101


def love_verdict(score):
    if score < 30:
        return "You're better off texting your ex's Spotify account at this point."
    if score < 60:
        return "You're emotionally stable…ish. Maybe flirt in the comments section?"
    if score < 85:
        return "Looking good. Just don't start a situationship out of boredom."
    return "You're ready. Go break hearts — respectfully."


def random_cat():
    try:
        response = requests.get("https://api.thecatapi.com/v1/images/search", timeout=4)
        response.raise_for_status()
        return response.json()[0]["url"]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


# ---- Routes -----------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def home():
    pickup = None
    if request.method == "POST":
        # Never show the same line twice in a row.
        choices = [line for line in PICKUP_LINES if line != session.get("last_pickup")]
        pickup = random.choice(choices)
        session["last_pickup"] = pickup
    return render_template("index.html", pickup=pickup, logged_in=bool(session.get("token_info")))


@app.route("/login")
def login():
    if not SPOTIFY_CONFIGURED:
        return render_template("error.html", message=NOT_CONFIGURED_MSG), 503
    return redirect(spotify_oauth().get_authorize_url())


@app.route("/callback")
def callback():
    if request.args.get("error") or not request.args.get("code"):
        return render_template("error.html", message="Spotify login was cancelled."), 400
    spotify_oauth().get_access_token(request.args["code"], check_cache=False)
    return redirect(url_for("top_artists"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/top-artists")
def top_artists():
    time_range = request.args.get("range", "short_term")
    if time_range not in TIME_RANGES:
        time_range = "short_term"

    if not SPOTIFY_CONFIGURED:
        return render_template("error.html", message=NOT_CONFIGURED_MSG), 503
    sp = spotify_client()
    if sp is None:
        return redirect(url_for("login"))
    try:
        results = sp.current_user_top_artists(limit=5, time_range=time_range)
    except spotipy.SpotifyException:
        session.clear()
        return render_template(
            "error.html",
            message="Spotify didn't let us in. If this app is in development mode, "
                    "your account needs to be added to its user list.",
        ), 502

    artists = [simplify(a) for a in results["items"]]
    session["artist_names"] = [a["name"] for a in artists]
    return render_results(artists, time_range, demo=False)


@app.route("/demo")
def demo():
    """Sample data so anyone can try the app without a Spotify login."""
    artists = DEMO_ARTISTS
    session["artist_names"] = [a["name"] for a in artists]
    return render_results(artists, "short_term", demo=True)


def render_results(artists, time_range, demo):
    meter, meter_verdict = basic_meter(artists)
    return render_template(
        "top_artists.html",
        artists=artists,
        roast=build_roast(artists),
        meter=meter,
        meter_verdict=meter_verdict,
        time_range=time_range,
        time_ranges=TIME_RANGES,
        demo=demo,
    )


@app.route("/love-score")
def love_score():
    names = session.get("artist_names") or []
    score = love_score_for([{"name": n} for n in names])
    cat = random_cat() if score < 30 else None
    return render_template(
        "love_score.html", score=score, roast=love_verdict(score),
        cat_img_url=cat, based_on_music=bool(names),
    )


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
