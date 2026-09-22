import sqlite3
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "change-moi-en-production"

DATA = "helpdesk.db"
CATEGORIES = ["Matériel", "Logiciel", "Réseau", "Compte"]
PRIORITES = ["Faible", "Moyenne", "Haute", "Critique"]


@app.before_request
def force_https():
    """Redirige vers HTTPS en production (Render transmet le protocole d'origine
    via ce header ; en local, X-Forwarded-Proto est absent donc rien ne se passe)."""
    if request.headers.get("X-Forwarded-Proto", "https") == "http":
        url = request.url.replace("http://", "https://", 1)
        return redirect(url, code=301)


@app.after_request
def add_security_headers(response):
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.route("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Disallow: /tickets",
        "Disallow: /dashboard",
        "Allow: /$",
        "Allow: /login",
        "Allow: /register",
        "Allow: /rgpd",
        "Allow: /cgu",
        f"Sitemap: {request.url_root}sitemap.xml",
    ]
    return "\n".join(lines), 200, {"Content-Type": "text/plain"}


@app.route("/sitemap.xml")
def sitemap_xml():
    pages = [
        (url_for("login", _external=True), "1.0"),
        (url_for("register", _external=True), "0.9"),
        (url_for("rgpd", _external=True), "0.3"),
        (url_for("cgu", _external=True), "0.3"),
    ]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, priority in pages:
        xml.append(f"<url><loc>{loc}</loc><priority>{priority}</priority></url>")
    xml.append("</urlset>")
    return "\n".join(xml), 200, {"Content-Type": "application/xml"}


@app.route("/rgpd")
def rgpd():
    return render_template("rgpd.html")


@app.route("/cgu")
def cgu():
    return render_template("cgu.html")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATA)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATA)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entreprise TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            titre TEXT NOT NULL,
            description TEXT NOT NULL,
            categorie TEXT,
            priorite TEXT,
            statut TEXT,
            utilisateur TEXT,
            technicien TEXT,
            date_creation TEXT,
            date_resolution TEXT,
            solution TEXT,
            historique TEXT,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        );
    """)
    db.commit()
    db.close()


def now():
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    if "user_id" not in session:
        return None
    return get_db().execute(
        "SELECT * FROM users WHERE id = ?", (session["user_id"],)
    ).fetchone()


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


# ---------- Auth ----------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        # Honeypot anti-spam : ce champ est invisible pour un humain (voir style.css
        # .hp-field) mais souvent rempli automatiquement par les bots.
        if request.form.get("site_web", ""):
            return redirect(url_for("login"))

        entreprise = request.form.get("entreprise", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not entreprise or not email or not password:
            flash("Remplis tous les champs.", "warning")
            return render_template("register.html")
        if "@" not in email or "." not in email.split("@")[-1]:
            flash("Adresse email invalide.", "warning")
            return render_template("register.html")
        if len(password) < 8:
            flash("Le mot de passe doit faire au moins 8 caractères.", "warning")
            return render_template("register.html")

        db = get_db()
        existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            flash("Un compte existe déjà avec cet email.", "warning")
            return render_template("register.html")

        db.execute(
            "INSERT INTO users (entreprise, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (entreprise, email, generate_password_hash(password), now()),
        )
        db.commit()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        session["user_id"] = user["id"]
        flash(f"Bienvenue {entreprise} !", "success")
        return redirect(url_for("home"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
        flash("Email ou mot de passe incorrect.", "warning")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Tickets (scoped à l'entreprise connectée) ----------

@app.route("/")
@login_required
def home():
    db = get_db()
    owner_id = session["user_id"]
    row = db.execute(
        """SELECT
            COUNT(*) as total,
            SUM(statut='Ouvert') as ouverts,
            SUM(statut='En cours') as en_cours,
            SUM(statut='Résolu') as resolus
           FROM tickets WHERE owner_id = ?""",
        (owner_id,),
    ).fetchone()
    stats = {k: (row[k] or 0) for k in row.keys()}
    return render_template("home.html", stats=stats)


@app.route("/tickets")
@login_required
def list_tickets():
    db = get_db()
    owner_id = session["user_id"]
    term = request.args.get("q", "").strip().lower()
    query = "SELECT * FROM tickets WHERE owner_id = ?"
    params = [owner_id]
    if term:
        query += " AND (LOWER(titre) LIKE ? OR LOWER(utilisateur) LIKE ? OR CAST(id AS TEXT) LIKE ?)"
        like = f"%{term}%"
        params += [like, like, like]
    query += " ORDER BY id DESC"
    tickets = db.execute(query, params).fetchall()
    return render_template("tickets.html", tickets=tickets, term=term)


@app.route("/tickets/create", methods=["GET", "POST"])
@login_required
def create_ticket():
    if request.method == "POST":
        if request.form.get("site_web", ""):
            return redirect(url_for("list_tickets"))

        u = request.form.get("utilisateur", "").strip()
        t = request.form.get("titre", "").strip()
        d = request.form.get("description", "").strip()
        if not u or not t or not d:
            flash("Remplis le nom, le titre et la description.", "warning")
            return render_template("create_ticket.html", categories=CATEGORIES, priorites=PRIORITES)

        db = get_db()
        date = now()
        db.execute(
            """INSERT INTO tickets
               (owner_id, titre, description, categorie, priorite, statut, utilisateur,
                technicien, date_creation, date_resolution, solution, historique)
               VALUES (?, ?, ?, ?, ?, 'Ouvert', ?, '', ?, '', '', ?)""",
            (
                session["user_id"], t, d,
                request.form.get("categorie", CATEGORIES[0]),
                request.form.get("priorite", PRIORITES[1]),
                u, date, f"[{{\"date\": \"{date}\", \"action\": \"Ticket créé\"}}]",
            ),
        )
        db.commit()
        flash("Le ticket a été créé avec succès !", "success")
        return redirect(url_for("list_tickets"))

    return render_template("create_ticket.html", categories=CATEGORIES, priorites=PRIORITES)


def get_owned_ticket(ticket_id):
    db = get_db()
    return db.execute(
        "SELECT * FROM tickets WHERE id = ? AND owner_id = ?",
        (ticket_id, session["user_id"]),
    ).fetchone()


@app.route("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id):
    import json
    t = get_owned_ticket(ticket_id)
    if not t:
        flash("Ticket introuvable.", "warning")
        return redirect(url_for("list_tickets"))
    t = dict(t)
    t["historique"] = json.loads(t["historique"] or "[]")
    return render_template("detail.html", t=t)


@app.route("/tickets/<int:ticket_id>/update", methods=["POST"])
@login_required
def update_status(ticket_id):
    import json
    status = request.form.get("statut")
    t = get_owned_ticket(ticket_id)
    if t and status:
        db = get_db()
        hist = json.loads(t["historique"] or "[]")
        hist.append({"date": now(), "action": f"Statut changé en : {status}"})
        technicien = t["technicien"] or ("Technicien IT" if status == "En cours" else "")
        date_resolution = now() if status == "Résolu" else t["date_resolution"]
        db.execute(
            "UPDATE tickets SET statut=?, technicien=?, date_resolution=?, historique=? WHERE id=?",
            (status, technicien, date_resolution, json.dumps(hist, ensure_ascii=False), ticket_id),
        )
        db.commit()
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/tickets/<int:ticket_id>/intervention", methods=["POST"])
@login_required
def intervention(ticket_id):
    import json
    value = request.form.get("solution", "").strip()
    t = get_owned_ticket(ticket_id)
    if t and value:
        db = get_db()
        hist = json.loads(t["historique"] or "[]")
        hist.append({"date": now(), "action": f"Intervention : {value}"})
        db.execute(
            "UPDATE tickets SET solution=?, historique=? WHERE id=?",
            (value, json.dumps(hist, ensure_ascii=False), ticket_id),
        )
        db.commit()
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    owner_id = session["user_id"]
    row = db.execute(
        """SELECT
            COUNT(*) as total,
            SUM(statut='Ouvert') as ouverts,
            SUM(statut='En cours') as en_cours,
            SUM(statut='Résolu') as resolus,
            SUM(priorite='Critique') as critiques
           FROM tickets WHERE owner_id = ?""",
        (owner_id,),
    ).fetchone()
    stats = {k: (row[k] or 0) for k in row.keys()}
    return render_template("dashboard.html", stats=stats)


init_db()

if __name__ == "__main__":
    app.run(debug=True)
    