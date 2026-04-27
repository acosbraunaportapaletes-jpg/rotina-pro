# RotinaPro

Automatize follow-ups, cobrancas e relatorios do seu negocio em 5 minutos.

## Como rodar

```bash
pip install -r requirements.txt
export SECRET_KEY="sua-chave-secreta"
python app.py
```

Acesse http://localhost:5000

## Features implementadas

- **Auth**: Cadastro e login com email+senha (hash werkzeug, sessao Flask)
- **Clientes**: CRUD completo com nome, telefone, email, notas
- **Follow-ups**: Agenda por cliente com status (pendente/feito/atrasado), filtros e marcacao via htmx
- **Cobrancas**: Registro de pagamentos com valor, vencimento, status (pendente/pago/vencido)
- **Dashboard**: Painel resumo com cobrancas vencidas, follow-ups atrasados, faturamento do mes

## Stack

- Python 3 + Flask
- SQLite (zero config)
- htmx (interacoes sem reload)
- Tailwind CSS via CDN

## Proximos passos sugeridos

- Notificacoes por email/WhatsApp para follow-ups atrasados
- Exportacao de relatorio financeiro em PDF/CSV
- Recorrencia automatica de cobrancas
- Multi-usuario com permissoes (equipe)
- Deploy em Railway/Render com PostgreSQL
