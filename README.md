# Planner Financeiro Inteligente (Streamlit)

Aplicação completa de gestão financeira pessoal e empresarial, construída em Streamlit 1.50.

## Recursos principais

- Sistema de login com:
  - Usuário master (admin) com aprovação de novos usuários.
  - Recuperação de senha via pergunta/resposta de segurança.
- Multiplos planners:
  - Planner pessoal ou empresarial para cada usuário.
  - Cada planner com base de dados própria (rendas, despesas e cartões separados).
- Gestão de rendas:
  - Tipos: fixa, comissão, premiação, extra, etc.
  - Recorrência: apenas este mês, todos os meses ou por X meses.
- Gestão de despesas:
  - Classificação por tipo (financiamento, luz, água, internet, impostos, aluguel, etc.).
- Gestão de cartões de crédito:
  - Cadastro de cartões por banco.
  - Cadastro de faturas por mês (atual, seguinte, etc.).
- Dashboard gerencial:
  - KPIs de renda, despesas e resultado (mês atual, anterior e projeção do próximo).
  - Percentual de comprometimento da renda pelas despesas, com limite configurável.
  - Gráficos interativos (linha de tendência e pizza de composição das despesas).
- Alertas inteligentes:
  - Alertas de contas a vencer em até 5 dias.
  - Alertas específicos para contas vencendo amanhã.
  - Badge de alerta quando o comprometimento da renda ultrapassar o limite definido.

## Como executar

1. Crie e ative um ambiente virtual (recomendado):

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate   # Windows
```

2. Instale as dependências:

```bash
pip install -r requirements.txt
```

3. Execute a aplicação:

```bash
streamlit run app.py
```

## Observações

- O banco de dados SQLite (`finance_manager.db`) será criado automaticamente na raiz do projeto.
- O sal de hash de senha é definido na função `hash_password`. Em produção, altere esse valor e trate via variáveis de ambiente.

Bom uso e bons insights financeiros! 💸
