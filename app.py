import os
import json
import sqlite3
import datetime
import functools
from flask import (
    Flask, request, session, redirect, url_for,
    render_template, flash, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DATABASE = os.environ.get("DATABASE_URL", "rotina_pro.db")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# --------------- DB helpers ---------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
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
        plan TEXT DEFAULT 'free',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS diagnostics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        business_type TEXT,
        team_size INTEGER,
        tools_current TEXT,
        pains TEXT,
        score TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS playbooks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        diagnostic_id INTEGER,
        title TEXT,
        content TEXT,
        niche TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (diagnostic_id) REFERENCES diagnostics(id)
    );
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        category TEXT,
        niche TEXT,
        content TEXT,
        is_pro BOOLEAN DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS checklist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        playbook_id INTEGER NOT NULL,
        title TEXT,
        status TEXT DEFAULT 'pending',
        completed_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (playbook_id) REFERENCES playbooks(id)
    );
    """)
    # Seed templates if empty
    cur = db.execute("SELECT COUNT(*) FROM templates")
    if cur.fetchone()[0] == 0:
        seed_templates(db)
    db.commit()
    db.close()


def seed_templates(db):
    templates = [
        ("Follow-up WhatsApp - Pos-consulta", "mensagem", "dentista",
         "Ola {{nome}}, tudo bem? Passando pra saber como voce esta apos a consulta de {{data}}. Qualquer duvida, estou por aqui! 😊\n\n[Enviar 24h apos a consulta via automacao no WhatsApp Business]", 0),
        ("Follow-up WhatsApp - Agendamento", "mensagem", "barbearia",
         "E ai {{nome}}! Ja faz {{dias}} dias desde o ultimo corte. Bora agendar o proximo? 💈\nLink: {{link_agendamento}}\n\n[Automatizar com disparo recorrente a cada 21 dias]", 0),
        ("Prompt IA - Criar post Instagram", "prompt", "agencia",
         "Voce e um social media especialista. Crie 5 opcoes de legenda para um post de {{tema}} para o cliente {{cliente}} no nicho {{nicho}}. Tom: {{tom}}. Inclua hashtags relevantes e CTA.", 0),
        ("Prompt IA - Resposta automatica", "prompt", "geral",
         "Voce e um assistente de atendimento da empresa {{empresa}}. Responda a seguinte mensagem do cliente de forma cordial e objetiva:\n\nMensagem: {{mensagem}}\n\nRegras: nao invente informacoes, direcione para humano se necessario.", 0),
        ("Planilha - Conciliacao financeira", "planilha", "geral",
         "MODELO DE PLANILHA:\n| Data | Descricao | Entrada | Saida | Saldo | Categoria | Status |\n|------|-----------|---------|-------|-------|-----------|--------|\n\nDica: Importe extrato bancario em CSV e use PROCV para conciliar automaticamente.", 0),
        ("Checklist - Onboarding cliente", "checklist", "agencia",
         "1. Enviar contrato digital (usar DocuSign/Clicksign)\n2. Coletar acessos (redes sociais, analytics, dominio)\n3. Agendar reuniao de kickoff\n4. Criar pasta compartilhada no Drive\n5. Configurar grupo WhatsApp do projeto\n6. Enviar questionario de briefing\n7. Definir cronograma de entregas", 0),
        ("Script - Cobranca gentil", "mensagem", "geral",
         "Ola {{nome}}, tudo bem? Notei que o pagamento ref. {{servico}} (vencimento {{data}}) esta em aberto. Consigo te ajudar com alguma duvida sobre o pagamento?\n\nPix: {{chave_pix}}\nBoleto: {{link_boleto}}\n\n[Automatizar: enviar 3 dias apos vencimento]", 0),
        ("Prompt IA - Relatorio semanal", "prompt", "agencia",
         "Com base nos seguintes dados de performance da semana:\n\n{{dados}}\n\nGere um relatorio executivo com: resumo geral, top 3 destaques, pontos de atencao e recomendacoes para proxima semana. Formato: bullet points objetivos.", 1),
    ]
    db.executemany(
        "INSERT INTO templates (title, category, niche, content, is_pro) VALUES (?, ?, ?, ?, ?)",
        templates,
    )


# --------------- Auth helpers ---------------

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Faca login para continuar.", "warning")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def current_user():
    if "user_id" not in session:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()


# --------------- LLM helper ---------------

def generate_playbook_llm(diagnostic):
    """Generate playbook via Anthropic API. Falls back to rule-based if no key."""
    prompt = (
        f"Voce e um consultor de automacao de negocios. Com base no diagnostico abaixo, "
        f"gere um playbook detalhado com passos concretos, ferramentas sugeridas e prompts prontos.\n\n"
        f"Tipo de negocio: {diagnostic['business_type']}\n"
        f"Tamanho da equipe: {diagnostic['team_size']}\n"
        f"Ferramentas atuais: {diagnostic['tools_current']}\n"
        f"Principais dores: {diagnostic['pains']}\n\n"
        f"Formato do playbook:\n"
        f"1. Titulo do playbook\n"
        f"2. Resumo do diagnostico (2 linhas)\n"
        f"3. 5-7 passos de automacao, cada um com:\n"
        f"   - Nome da automacao\n"
        f"   - Ferramenta sugerida\n"
        f"   - Como implementar (3-4 linhas)\n"
        f"   - Prompt pronto (se aplicavel)\n"
        f"4. Cronograma sugerido de implementacao\n"
        f"5. Metricas para acompanhar\n\n"
        f"Responda em portugues brasileiro."
    )

    if ANTHROPIC_API_KEY:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            print(f"LLM error: {e}")

    # Fallback rule-based generation
    btype = diagnostic["business_type"]
    pains = diagnostic["pains"]
    tools_map = {
        "dentista": [
            ("Agendamento automatico", "Google Calendar + WhatsApp API", "Configure lembretes automaticos 24h antes da consulta via WhatsApp Business API."),
            ("Follow-up pos-consulta", "WhatsApp + Zapier", "Envie mensagem automatica 24h apos consulta perguntando sobre recuperacao."),
            ("Controle financeiro", "Planilha Google + AppSheet", "Crie app simples para registrar pagamentos e gerar relatorio mensal."),
            ("Captacao de avaliacoes", "Google Meu Negocio + automacao", "Envie link para avaliacao 3 dias apos consulta para pacientes satisfeitos."),
            ("Prontuario digital", "Google Forms + Sheets", "Digitalize fichas de pacientes com formulario padronizado."),
        ],
        "barbearia": [
            ("Agendamento online", "Calendly + WhatsApp", "Disponibilize link de agendamento que sincroniza com sua agenda."),
            ("Lembrete de retorno", "WhatsApp API", "Dispare mensagem automatica a cada 21 dias convidando para novo corte."),
            ("Programa de fidelidade", "Planilha + WhatsApp", "Controle visitas e envie cupom automatico a cada 10 cortes."),
            ("Gestao de caixa", "Planilha automatizada", "Use planilha com formulas para fechar caixa diariamente em 2 minutos."),
            ("Marketing local", "Instagram + IA", "Use prompts de IA para gerar conteudo semanal de antes/depois."),
        ],
    }
    default_steps = [
        ("Atendimento automatico", "WhatsApp Business + chatbot", "Configure respostas automaticas para perguntas frequentes."),
        ("Agendamento digital", "Calendly ou Google Calendar", "Elimine agendamento manual com link de auto-agendamento."),
        ("Follow-up automatico", "Zapier + WhatsApp/Email", "Crie sequencia de follow-up pos-venda com 3 mensagens."),
        ("Controle financeiro", "Planilha Google automatizada", "Centralize entradas e saidas com categorizacao automatica."),
        ("Relatorios com IA", "ChatGPT/Claude + dados", "Use prompts para gerar relatorios semanais a partir dos seus dados."),
    ]
    steps = tools_map.get(btype, default_steps)
    lines = [f"# Playbook de Automacao - {btype.title()}\n"]
    lines.append(f"**Diagnostico:** Negocio do tipo {btype} com equipe de {diagnostic['team_size']} pessoas.")
    lines.append(f"**Principais dores:** {pains}\n")
    lines.append("## Passos de Implementacao\n")
    for i, (name, tool, desc) in enumerate(steps, 1):
        lines.append(f"### {i}. {name}")
        lines.append(f"**Ferramenta:** {tool}")
        lines.append(f"{desc}\n")
    lines.append("## Cronograma Sugerido\n")
    lines.append("- Semana 1-2: Implementar passos 1 e 2")
    lines.append("- Semana 3-4: Implementar passos 3 e 4")
    lines.append("- Semana 5+: Implementar passo 5 e otimizar\n")
    lines.append("## Metricas para Acompanhar\n")
    lines.append("- Tempo economizado por semana (horas)")
    lines.append("- Taxa de no-show (agendamentos)")
    lines.append("- Taxa de resposta de follow-up")
    lines.append("- Receita recorrente mensal")
    return "\n".join(lines)


# --------------- Quiz questions ---------------

QUIZ_QUESTIONS = [
    {"id": "business_type", "text": "Qual o tipo do seu negocio?", "type": "select",
     "options": ["dentista", "barbearia", "agencia", "restaurante", "ecommerce", "freelancer", "outro"]},
    {"id": "team_size", "text": "Quantas pessoas trabalham no seu negocio?", "type": "select",
     "options": ["1", "2-5", "6-15", "16-50", "50+"]},
    {"id": "tools_current", "text": "Quais ferramentas voce usa hoje?", "type": "checkbox",
     "options": ["WhatsApp", "Excel/Planilhas", "Instagram", "Google Calendar", "Sistema proprio", "Papel/caderno", "Nenhuma"]},
    {"id": "pain_atendimento", "text": "Atendimento ao cliente toma muito tempo?", "type": "select",
     "options": ["Sim, demais", "Um pouco", "Esta ok", "Ja automatizei"]},
    {"id": "pain_agendamento", "text": "Como e feito o agendamento?", "type": "select",
     "options": ["WhatsApp manual", "Telefone", "Sistema online", "Nao tenho agendamento"]},
    {"id": "pain_financeiro", "text": "Como voce controla as financas?", "type": "select",
     "options": ["Caderno/papel", "Planilha manual", "Software financeiro", "Contador faz tudo"]},
    {"id": "pain_marketing", "text": "Como voce faz marketing?", "type": "select",
     "options": ["Nao faco", "Posts manuais", "Agencia externa", "Automacao parcial"]},
    {"id": "goal", "text": "Qual seu principal objetivo com automacao?", "type": "select",
     "options": ["Economizar tempo", "Reduzir erros", "Aumentar vendas", "Melhorar atendimento", "Tudo acima"]},
]

TEAM_SIZE_MAP = {"1": 1, "2-5": 3, "6-15": 10, "16-50": 30, "50+": 60}


# --------------- Routes ---------------

@app.route("/")
def index():
    user = current_user()
    return render_template("index.html", user=user)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("login.html", mode="register")
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not email or not password:
        flash("Preencha todos os campos.", "error")
        return render_template("login.html", mode="register")
    if len(password) < 6:
        flash("Senha deve ter pelo menos 6 caracteres.", "error")
        return render_template("login.html", mode="register")
    db = get_db()
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        flash("Email ja cadastrado.", "error")
        return render_template("login.html", mode="register")
    pw_hash = generate_password_hash(password)
    cur = db.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, pw_hash))
    db.commit()
    session["user_id"] = cur.lastrowid
    session["user_email"] = email
    flash("Conta criada com sucesso!", "success")
    return redirect(url_for("diagnostic_form"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", mode="login")
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        flash("Email ou senha incorretos.", "error")
        return render_template("login.html", mode="login")
    session["user_id"] = user["id"]
    session["user_email"] = user["email"]
    flash("Login realizado!", "success")
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/diagnostico", methods=["GET"])
@login_required
def diagnostic_form():
    return render_template("diagnostic.html", questions=QUIZ_QUESTIONS)


@app.route("/diagnostico", methods=["POST"])
@login_required
def diagnostic_submit():
    db = get_db()
    business_type = request.form.get("business_type", "outro")
    team_size_raw = request.form.get("team_size", "1")
    team_size = TEAM_SIZE_MAP.get(team_size_raw, 1)
    tools_current = ", ".join(request.form.getlist("tools_current")) or "Nenhuma"
    pains = []
    for q in QUIZ_QUESTIONS:
        if q["id"].startswith("pain_"):
            val = request.form.get(q["id"], "")
            pains.append(f"{q['text']}: {val}")
    goal = request.form.get("goal", "")
    pains.append(f"Objetivo: {goal}")
    pains_text = "; ".join(pains)

    score = {
        "atendimento": 1 if request.form.get("pain_atendimento") in ("Sim, demais", "Um pouco") else 0,
        "agendamento": 1 if request.form.get("pain_agendamento") in ("WhatsApp manual", "Telefone") else 0,
        "financeiro": 1 if request.form.get("pain_financeiro") in ("Caderno/papel", "Planilha manual") else 0,
        "marketing": 1 if request.form.get("pain_marketing") in ("Nao faco", "Posts manuais") else 0,
    }

    cur = db.execute(
        "INSERT INTO diagnostics (user_id, business_type, team_size, tools_current, pains, score) VALUES (?, ?, ?, ?, ?, ?)",
        (session["user_id"], business_type, team_size, tools_current, pains_text, json.dumps(score)),
    )
    diagnostic_id = cur.lastrowid
    db.commit()

    diag_data = {
        "business_type": business_type,
        "team_size": team_size,
        "tools_current": tools_current,
        "pains": pains_text,
    }
    content = generate_playbook_llm(diag_data)
    title = f"Playbook - {business_type.title()}"

    cur2 = db.execute(
        "INSERT INTO playbooks (user_id, diagnostic_id, title, content, niche) VALUES (?, ?, ?, ?, ?)",
        (session["user_id"], diagnostic_id, title, content, business_type),
    )
    playbook_id = cur2.lastrowid

    # Create checklist items from playbook steps
    checklist_titles = []
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("### "):
            checklist_titles.append(stripped.replace("### ", ""))
        elif stripped.startswith("- Semana"):
            checklist_titles.append(stripped.lstrip("- "))
    if not checklist_titles:
        checklist_titles = ["Revisar playbook", "Implementar primeiro passo", "Testar automacao", "Medir resultados"]
    for ct in checklist_titles:
        db.execute(
            "INSERT INTO checklist_items (user_id, playbook_id, title) VALUES (?, ?, ?)",
            (session["user_id"], playbook_id, ct),
        )
    db.commit()
    return redirect(url_for("playbook_view", id=playbook_id))


@app.route("/playbook/<int:id>")
@login_required
def playbook_view(id):
    db = get_db()
    playbook = db.execute("SELECT * FROM playbooks WHERE id = ? AND user_id = ?", (id, session["user_id"])).fetchone()
    if not playbook:
        flash("Playbook nao encontrado.", "error")
        return redirect(url_for("dashboard"))
    items = db.execute(
        "SELECT * FROM checklist_items WHERE playbook_id = ? AND user_id = ? ORDER BY id",
        (id, session["user_id"]),
    ).fetchall()
    total = len(items)
    done = sum(1 for i in items if i["status"] == "done")
    progress = int((done / total) * 100) if total else 0
    return render_template("playbook.html", playbook=playbook, items=items, progress=progress)


@app.route("/checklist/<int:id>/toggle", methods=["POST"])
@login_required
def checklist_toggle(id):
    db = get_db()
    item = db.execute("SELECT * FROM checklist_items WHERE id = ? AND user_id = ?", (id, session["user_id"])).fetchone()
    if not item:
        return "Not found", 404
    new_status = "pending" if item["status"] == "done" else "done"
    completed_at = datetime.datetime.now().isoformat() if new_status == "done" else None
    db.execute("UPDATE checklist_items SET status = ?, completed_at = ? WHERE id = ?", (new_status, completed_at, id))
    db.commit()
    # Return updated item HTML for htmx
    item = db.execute("SELECT * FROM checklist_items WHERE id = ?", (id,)).fetchone()
    checked = "checked" if item["status"] == "done" else ""
    line_through = "line-through text-gray-400" if item["status"] == "done" else ""
    return f'''
    <div class="flex items-center gap-3 p-3 bg-gray-800 rounded-lg" id="item-{item['id']}">
        <input type="checkbox" {checked}
            hx-post="/checklist/{item['id']}/toggle"
            hx-target="#item-{item['id']}"
            hx-swap="outerHTML"
            class="w-5 h-5 accent-emerald-500 cursor-pointer">
        <span class="{line_through}">{item['title']}</span>
    </div>
    '''


@app.route("/templates")
@login_required
def templates_list():
    db = get_db()
    niche = request.args.get("niche", "")
    category = request.args.get("category", "")
    query = "SELECT * FROM templates WHERE 1=1"
    params = []
    if niche:
        query += " AND niche = ?"
        params.append(niche)
    if category:
        query += " AND category = ?"
        params.append(category)
    tpls = db.execute(query, params).fetchall()
    niches = [r["niche"] for r in db.execute("SELECT DISTINCT niche FROM templates").fetchall()]
    categories = [r["category"] for r in db.execute("SELECT DISTINCT category FROM templates").fetchall()]
    return render_template("templates.html", templates=tpls, niches=niches, categories=categories,
                           current_niche=niche, current_category=category)


@app.route("/templates/<int:id>")
@login_required
def template_view(id):
    db = get_db()
    tpl = db.execute("SELECT * FROM templates WHERE id = ?", (id,)).fetchone()
    if not tpl:
        flash("Template nao encontrado.", "error")
        return redirect(url_for("templates_list"))
    return render_template("template_view.html", template=tpl)


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]
    playbooks = db.execute("SELECT * FROM playbooks WHERE user_id = ? ORDER BY created_at DESC", (uid,)).fetchall()
    diagnostics = db.execute("SELECT * FROM diagnostics WHERE user_id = ? ORDER BY created_at DESC", (uid,)).fetchall()
    total_items = db.execute("SELECT COUNT(*) as c FROM checklist_items WHERE user_id = ?", (uid,)).fetchone()["c"]
    done_items = db.execute("SELECT COUNT(*) as c FROM checklist_items WHERE user_id = ? AND status = 'done'", (uid,)).fetchone()["c"]
    progress = int((done_items / total_items) * 100) if total_items else 0
    return render_template("dashboard.html", playbooks=playbooks, diagnostics=diagnostics,
                           progress=progress, total_items=total_items, done_items=done_items)


# --------------- Startup ---------------

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
