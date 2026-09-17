import json
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "change-moi-en-production"

DATA = "tickets.json"

CATEGORIES = ["Matériel", "Logiciel", "Réseau", "Compte"]
PRIORITES = ["Faible", "Moyenne", "Haute", "Critique"]


def now():
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def load():
    if not os.path.exists(DATA):
        return []
    with open(DATA, "r", encoding="utf-8") as f:
        return json.load(f)


def save(tickets):
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(tickets, f, indent=4, ensure_ascii=False)


def next_id(tickets):
    return max([t["id"] for t in tickets], default=0) + 1


def find(tickets, ticket_id):
    return next((t for t in tickets if t["id"] == ticket_id), None)


@app.route("/")
def home():
    tickets = load()
    stats = {
        "total": len(tickets),
        "ouverts": sum(t["statut"] == "Ouvert" for t in tickets),
        "en_cours": sum(t["statut"] == "En cours" for t in tickets),
        "resolus": sum(t["statut"] == "Résolu" for t in tickets),
    }
    return render_template("home.html", stats=stats)


@app.route("/tickets")
def list_tickets():
    tickets = load()
    term = request.args.get("q", "").strip().lower()
    if term:
        tickets = [
            t for t in tickets
            if term in str(t["id"]).lower()
            or term in t["titre"].lower()
            or term in t["utilisateur"].lower()
        ]
    tickets.sort(key=lambda t: t["id"], reverse=True)
    return render_template("tickets.html", tickets=tickets, term=term)


@app.route("/tickets/create", methods=["GET", "POST"])
def create_ticket():
    if request.method == "POST":
        u = request.form.get("utilisateur", "").strip()
        t = request.form.get("titre", "").strip()
        d = request.form.get("description", "").strip()
        if not u or not t or not d:
            flash("Remplis le nom, le titre et la description.", "warning")
            return render_template(
                "create_ticket.html", categories=CATEGORIES, priorites=PRIORITES
            )

        tickets = load()
        ident = next_id(tickets)
        date = now()
        tickets.append({
            "id": ident,
            "titre": t,
            "description": d,
            "categorie": request.form.get("categorie", CATEGORIES[0]),
            "priorite": request.form.get("priorite", PRIORITES[1]),
            "statut": "Ouvert",
            "utilisateur": u,
            "technicien": "",
            "date_creation": date,
            "date_resolution": "",
            "solution": "",
            "historique": [{"date": date, "action": "Ticket créé"}],
        })
        save(tickets)
        flash(f"Le ticket #{ident} a été créé avec succès !", "success")
        return redirect(url_for("list_tickets"))

    return render_template(
        "create_ticket.html", categories=CATEGORIES, priorites=PRIORITES
    )


@app.route("/tickets/<int:ticket_id>")
def ticket_detail(ticket_id):
    tickets = load()
    t = find(tickets, ticket_id)
    if not t:
        flash("Ticket introuvable.", "warning")
        return redirect(url_for("list_tickets"))
    return render_template("detail.html", t=t)


@app.route("/tickets/<int:ticket_id>/update", methods=["POST"])
def update_status(ticket_id):
    status = request.form.get("statut")
    tickets = load()
    t = find(tickets, ticket_id)
    if t and status:
        t["statut"] = status
        t["historique"].append({"date": now(), "action": f"Statut changé en : {status}"})
        if status == "En cours" and not t["technicien"]:
            t["technicien"] = "Technicien IT"
        if status == "Résolu":
            t["date_resolution"] = now()
        save(tickets)
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/tickets/<int:ticket_id>/intervention", methods=["POST"])
def intervention(ticket_id):
    value = request.form.get("solution", "").strip()
    tickets = load()
    t = find(tickets, ticket_id)
    if t and value:
        t["solution"] = value
        t["historique"].append({"date": now(), "action": f"Intervention : {value}"})
        save(tickets)
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/dashboard")
def dashboard():
    tickets = load()
    stats = {
        "total": len(tickets),
        "ouverts": sum(t["statut"] == "Ouvert" for t in tickets),
        "en_cours": sum(t["statut"] == "En cours" for t in tickets),
        "resolus": sum(t["statut"] == "Résolu" for t in tickets),
        "critiques": sum(t["priorite"] == "Critique" for t in tickets),
    }
    return render_template("dashboard.html", stats=stats)


if __name__ == "__main__":
    app.run(debug=True)
