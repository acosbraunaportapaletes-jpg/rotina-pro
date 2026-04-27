import os
import sqlite3
from datetime import datetime, date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, g, abort
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DATABASE = os.environ.get("DATABASE_URL", "rotina.db")

# ── DB helpers ──────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        biz_name TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        phone TEXT DEFAULT '',
        email TEXT DEFAULT '',
        notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS followups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        client_id INTEGER NOT NULL REFERENCES clients(id),
        due_date DATE NOT NULL,
        status TEXT DEFAULT 'pending',
        note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS billings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        client_id INTEGER NOT NULL REFERENCES clients(id),
        amount_cents INTEGER NOT NULL,
        due_date DATE NOT NULL,
        status TEXT DEFAULT 'pending',
        paid_at TIMESTAMP,
        description TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db.close()


# ── Auth helpers ────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


def current_user_id():
    return session.get("user_id")


# ── Template helpers ────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {"today": date.today().isoformat()}


@app.template_filter("brl")
def brl_filter(cents):
    if cents is None:
        cents = 0
    reais = cents / 100
    return f"R$ {reais:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ── Routes: Landing / Auth ──────────────────────────────────────────────

@app.route("/")
def landing():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        biz_name = request.form.get("biz_name", "").strip()
        if not email or not password:
            flash("Email e senha obrigatorios.", "error")
            return render_template("register.html")
        if len(password) < 6:
            flash("Senha deve ter pelo menos 6 caracteres.", "error")
            return render_template("register.html")
        db = get_db()
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            flash("Email ja cadastrado.", "error")
            return render_template("register.html")
        db.execute(
            "INSERT INTO users (email, password_hash, biz_name) VALUES (?,?,?)",
            (email, generate_password_hash(password), biz_name),
        )
        db.commit()
        user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        session["user_id"] = user["id"]
        session["user_email"] = email
        flash("Conta criada com sucesso!", "success")
        return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            flash("Login realizado!", "success")
            return redirect(url_for("dashboard"))
        flash("Credenciais invalidas.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("landing"))


# ── Dashboard ───────────────────────────────────────────────────────────

@app.route("/dash")
@login_required
def dashboard():
    db = get_db()
    uid = current_user_id()
    today_str = date.today().isoformat()
    first_of_month = date.today().replace(day=1).isoformat()

    overdue_followups = db.execute(
        "SELECT f.*, c.name as client_name FROM followups f "
        "JOIN clients c ON c.id=f.client_id "
        "WHERE f.user_id=? AND f.status='pending' AND f.due_date < ?",
        (uid, today_str),
    ).fetchall()

    upcoming_followups = db.execute(
        "SELECT f.*, c.name as client_name FROM followups f "
        "JOIN clients c ON c.id=f.client_id "
        "WHERE f.user_id=? AND f.status='pending' AND f.due_date >= ? "
        "ORDER BY f.due_date LIMIT 5",
        (uid, today_str),
    ).fetchall()

    overdue_billings = db.execute(
        "SELECT b.*, c.name as client_name FROM billings b "
        "JOIN clients c ON c.id=b.client_id "
        "WHERE b.user_id=? AND b.status='pending' AND b.due_date < ?",
        (uid, today_str),
    ).fetchall()

    month_revenue = db.execute(
        "SELECT COALESCE(SUM(amount_cents),0) as total FROM billings "
        "WHERE user_id=? AND status='paid' AND paid_at >= ?",
        (uid, first_of_month),
    ).fetchone()["total"]

    total_clients = db.execute(
        "SELECT COUNT(*) as c FROM clients WHERE user_id=?", (uid,)
    ).fetchone()["c"]

    pending_billings_total = db.execute(
        "SELECT COALESCE(SUM(amount_cents),0) as total FROM billings "
        "WHERE user_id=? AND status='pending'",
        (uid,),
    ).fetchone()["total"]

    return render_template(
        "dashboard.html",
        overdue_followups=overdue_followups,
        upcoming_followups=upcoming_followups,
        overdue_billings=overdue_billings,
        month_revenue=month_revenue,
        total_clients=total_clients,
        pending_billings_total=pending_billings_total,
    )


# ── Clients CRUD ────────────────────────────────────────────────────────

@app.route("/clients", methods=["GET", "POST"])
@login_required
def clients():
    db = get_db()
    uid = current_user_id()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()
        if not name:
            flash("Nome e obrigatorio.", "error")
        else:
            db.execute(
                "INSERT INTO clients (user_id,name,phone,email,notes) VALUES (?,?,?,?,?)",
                (uid, name, phone, email, notes),
            )
            db.commit()
            flash("Cliente adicionado!", "success")
        if request.headers.get("HX-Request"):
            clients_list = db.execute(
                "SELECT * FROM clients WHERE user_id=? ORDER BY name", (uid,)
            ).fetchall()
            return render_template("partials/client_list.html", clients=clients_list)
    clients_list = db.execute(
        "SELECT * FROM clients WHERE user_id=? ORDER BY name", (uid,)
    ).fetchall()
    return render_template("clients.html", clients=clients_list)


@app.route("/clients/<int:id>", methods=["GET", "PUT", "DELETE"])
@login_required
def client_detail(id):
    db = get_db()
    uid = current_user_id()
    client = db.execute(
        "SELECT * FROM clients WHERE id=? AND user_id=?", (id, uid)
    ).fetchone()
    if not client:
        abort(404)

    if request.method == "DELETE":
        db.execute("DELETE FROM followups WHERE client_id=? AND user_id=?", (id, uid))
        db.execute("DELETE FROM billings WHERE client_id=? AND user_id=?", (id, uid))
        db.execute("DELETE FROM clients WHERE id=? AND user_id=?", (id, uid))
        db.commit()
        if request.headers.get("HX-Request"):
            return ""
        flash("Cliente removido.", "success")
        return redirect(url_for("clients"))

    if request.method == "PUT":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()
        db.execute(
            "UPDATE clients SET name=?,phone=?,email=?,notes=? WHERE id=? AND user_id=?",
            (name, phone, email, notes, id, uid),
        )
        db.commit()
        flash("Cliente atualizado!", "success")
        if request.headers.get("HX-Request"):
            client = db.execute("SELECT * FROM clients WHERE id=?", (id,)).fetchone()
            return render_template("partials/client_card.html", client=client)
        return redirect(url_for("client_detail", id=id))

    followups_list = db.execute(
        "SELECT * FROM followups WHERE client_id=? AND user_id=? ORDER BY due_date DESC",
        (id, uid),
    ).fetchall()
    billings_list = db.execute(
        "SELECT * FROM billings WHERE client_id=? AND user_id=? ORDER BY due_date DESC",
        (id, uid),
    ).fetchall()
    return render_template(
        "client_detail.html", client=client, followups=followups_list, billings=billings_list
    )


# ── Follow-ups ──────────────────────────────────────────────────────────

@app.route("/followups", methods=["GET", "POST"])
@login_required
def followups():
    db = get_db()
    uid = current_user_id()
    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        due_date = request.form.get("due_date", "")
        note = request.form.get("note", "").strip()
        if not client_id or not due_date:
            flash("Cliente e data sao obrigatorios.", "error")
        else:
            db.execute(
                "INSERT INTO followups (user_id,client_id,due_date,note) VALUES (?,?,?,?)",
                (uid, client_id, due_date, note),
            )
            db.commit()
            flash("Follow-up criado!", "success")
        if request.headers.get("HX-Request"):
            return _render_followup_list(db, uid, "")

    status_filter = request.args.get("status", "")
    clients_list = db.execute(
        "SELECT id,name FROM clients WHERE user_id=? ORDER BY name", (uid,)
    ).fetchall()
    return render_template(
        "followups.html",
        followups=_get_followups(db, uid, status_filter),
        clients=clients_list,
        status_filter=status_filter,
    )


def _get_followups(db, uid, status_filter=""):
    today_str = date.today().isoformat()
    if status_filter == "overdue":
        return db.execute(
            "SELECT f.*, c.name as client_name FROM followups f "
            "JOIN clients c ON c.id=f.client_id "
            "WHERE f.user_id=? AND f.status='pending' AND f.due_date < ? "
            "ORDER BY f.due_date",
            (uid, today_str),
        ).fetchall()
    if status_filter in ("pending", "done"):
        return db.execute(
            "SELECT f.*, c.name as client_name FROM followups f "
            "JOIN clients c ON c.id=f.client_id "
            "WHERE f.user_id=? AND f.status=? ORDER BY f.due_date",
            (uid, status_filter),
        ).fetchall()
    return db.execute(
        "SELECT f.*, c.name as client_name FROM followups f "
        "JOIN clients c ON c.id=f.client_id "
        "WHERE f.user_id=? ORDER BY f.due_date",
        (uid,),
    ).fetchall()


def _render_followup_list(db, uid, status_filter):
    return render_template("partials/followup_list.html", followups=_get_followups(db, uid, status_filter))


@app.route("/followups/<int:id>/done", methods=["POST"])
@login_required
def followup_done(id):
    db = get_db()
    uid = current_user_id()
    db.execute(
        "UPDATE followups SET status='done', completed_at=? WHERE id=? AND user_id=?",
        (datetime.now().isoformat(), id, uid),
    )
    db.commit()
    if request.headers.get("HX-Request"):
        f = db.execute(
            "SELECT f.*, c.name as client_name FROM followups f "
            "JOIN clients c ON c.id=f.client_id WHERE f.id=?",
            (id,),
        ).fetchone()
        return render_template("partials/followup_row.html", f=f)
    return redirect(url_for("followups"))


# ── Billings ────────────────────────────────────────────────────────────

@app.route("/billings", methods=["GET", "POST"])
@login_required
def billings():
    db = get_db()
    uid = current_user_id()
    if request.method == "POST":
        client_id = request.form.get("client_id", type=int)
        amount = request.form.get("amount", "")
        due_date = request.form.get("due_date", "")
        description = request.form.get("description", "").strip()
        if not client_id or not amount or not due_date:
            flash("Cliente, valor e data sao obrigatorios.", "error")
        else:
            amount_cents = int(float(amount.replace(",", ".")) * 100)
            db.execute(
                "INSERT INTO billings (user_id,client_id,amount_cents,due_date,description) "
                "VALUES (?,?,?,?,?)",
                (uid, client_id, amount_cents, due_date, description),
            )
            db.commit()
            flash("Cobranca registrada!", "success")
        if request.headers.get("HX-Request"):
            return _render_billing_list(db, uid)

    clients_list = db.execute(
        "SELECT id,name FROM clients WHERE user_id=? ORDER BY name", (uid,)
    ).fetchall()
    billings_list = db.execute(
        "SELECT b.*, c.name as client_name FROM billings b "
        "JOIN clients c ON c.id=b.client_id "
        "WHERE b.user_id=? ORDER BY b.due_date DESC",
        (uid,),
    ).fetchall()
    return render_template("billings.html", billings=billings_list, clients=clients_list)


def _render_billing_list(db, uid):
    billings_list = db.execute(
        "SELECT b.*, c.name as client_name FROM billings b "
        "JOIN clients c ON c.id=b.client_id "
        "WHERE b.user_id=? ORDER BY b.due_date DESC",
        (uid,),
    ).fetchall()
    return render_template("partials/billing_list.html", billings=billings_list)


@app.route("/billings/<int:id>/pay", methods=["POST"])
@login_required
def billing_pay(id):
    db = get_db()
    uid = current_user_id()
    db.execute(
        "UPDATE billings SET status='paid', paid_at=? WHERE id=? AND user_id=?",
        (datetime.now().isoformat(), id, uid),
    )
    db.commit()
    if request.headers.get("HX-Request"):
        b = db.execute(
            "SELECT b.*, c.name as client_name FROM billings b "
            "JOIN clients c ON c.id=b.client_id WHERE b.id=?",
            (id,),
        ).fetchone()
        return render_template("partials/billing_row.html", b=b)
    return redirect(url_for("billings"))


# ── Startup ─────────────────────────────────────────────────────────────

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
