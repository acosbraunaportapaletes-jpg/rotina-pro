# RotinaPro

Diagnostico de automacao + playbooks prontos pra seu negocio em 5 minutos.

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env   # edite com suas chaves
python app.py
```

Acesse `http://localhost:5000`.

## Features implementadas

- **Auth** - Registro e login com email+senha (hash via werkzeug, sessao Flask)
- **Diagnostico** - Quiz de 8 perguntas que classifica o perfil operacional
- **Playbook** - Geracao de playbook personalizado via LLM (Anthropic Claude) com fallback rule-based
- **Templates** - Biblioteca de templates acionaveis (mensagens WhatsApp, prompts IA, planilhas) filtrados por nicho e categoria
- **Checklist** - Checklist interativo (htmx) com progresso por playbook
- **Dashboard** - Painel com metricas de progresso e playbooks salvos

## Stack

- Python 3 + Flask
- SQLite (banco local, auto-criado no startup)
- htmx (interatividade sem JS custom)
- Tailwind CSS (via CDN)
- Anthropic API (opcional - funciona sem chave com fallback)

## Proximos passos

- Integracao com WhatsApp Business API para disparo real
- Plano Pro com templates exclusivos e playbooks ilimitados
- Exportar playbook em PDF
- Notificacoes por email de progresso
- Multi-idioma
