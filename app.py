
import streamlit as st
import sqlite3
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import hashlib
from typing import Optional, Tuple, Dict, Any, List

DB_FILE = "finance_manager.db"

# ---------- DB LAYER ----------

def get_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT,
        password_hash TEXT NOT NULL,
        is_active INTEGER DEFAULT 0,
        is_master INTEGER DEFAULT 0,
        recovery_question TEXT,
        recovery_answer_hash TEXT,
        created_at TEXT
    );
    """)

    # planners
    cur.execute("""
    CREATE TABLE IF NOT EXISTS planners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        owner_user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        alert_threshold REAL DEFAULT 0.8,
        currency TEXT DEFAULT 'R$',
        created_at TEXT,
        FOREIGN KEY(owner_user_id) REFERENCES users(id)
    );
    """)

    # incomes
    cur.execute("""
    CREATE TABLE IF NOT EXISTS incomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        planner_id INTEGER NOT NULL,
        description TEXT NOT NULL,
        income_type TEXT NOT NULL,
        amount REAL NOT NULL,
        start_date TEXT NOT NULL,
        recurrence TEXT NOT NULL,
        months_count INTEGER,
        is_active INTEGER DEFAULT 1,
        created_at TEXT,
        FOREIGN KEY(planner_id) REFERENCES planners(id)
    );
    """)

    # expenses (com is_paid)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        planner_id INTEGER NOT NULL,
        description TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        due_date TEXT NOT NULL,
        is_paid INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY(planner_id) REFERENCES planners(id)
    );
    """)

    # tentativa de adicionar coluna is_paid em bases antigas
    try:
        cur.execute("ALTER TABLE expenses ADD COLUMN is_paid INTEGER DEFAULT 0;")
        conn.commit()
    except Exception:
        pass

    # credit cards
    cur.execute("""
    CREATE TABLE IF NOT EXISTS credit_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        planner_id INTEGER NOT NULL,
        bank_name TEXT NOT NULL,
        card_name TEXT,
        created_at TEXT,
        FOREIGN KEY(planner_id) REFERENCES planners(id)
    );
    """)

    # credit card invoices (já com is_paid)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS credit_card_invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_id INTEGER NOT NULL,
        invoice_month TEXT NOT NULL,
        amount_due REAL NOT NULL,
        due_date TEXT NOT NULL,
        is_paid INTEGER DEFAULT 0,
        created_at TEXT,
        FOREIGN KEY(card_id) REFERENCES credit_cards(id)
    );
    """)

    # savings_adjustments (aportes/gastos que impactam saldo acumulado)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS savings_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        planner_id INTEGER NOT NULL,
        description TEXT NOT NULL,
        amount REAL NOT NULL,
        movement_date TEXT NOT NULL,
        movement_type TEXT NOT NULL, -- 'aporte' ou 'gasto'
        created_at TEXT,
        FOREIGN KEY(planner_id) REFERENCES planners(id)
    );
    """)

    conn.commit()

    # ensure master user exists
    cur.execute("SELECT COUNT(*) as c FROM users WHERE is_master = 1;")
    has_master = cur.fetchone()["c"] > 0
    if not has_master:
        now = datetime.utcnow().isoformat()
        pwd_hash = hash_password("admin")
        cur.execute("""
        INSERT OR IGNORE INTO users(username, email, password_hash, is_active, is_master, created_at)
        VALUES(?,?,?,?,?,?)
        """, ("admin", "admin@example.com", pwd_hash, 1, 1, now))
        conn.commit()
    conn.close()

# ---------- SECURITY / AUTH ----------

def hash_password(password: str) -> str:
    salt = "static_salt_please_change"
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()

def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash

def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return row

def create_user(username: str, email: str, password: str,
                recovery_question: str, recovery_answer: str) -> Tuple[bool, str]:
    if get_user_by_username(username):
        return False, "Usuário já existe."
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    pwd_hash = hash_password(password)
    rec_hash = hash_password(recovery_answer) if recovery_answer else None
    try:
        cur.execute("""
        INSERT INTO users(username, email, password_hash, is_active, is_master, recovery_question, recovery_answer_hash, created_at)
        VALUES(?,?,?,?,?,?,?,?)
        """, (username, email, pwd_hash, 0, 0, recovery_question, rec_hash, now))
        conn.commit()
        return True, "Usuário criado com sucesso! Aguarde aprovação do usuário master."
    except Exception as e:
        return False, f"Erro ao criar usuário: {e}"
    finally:
        conn.close()

def approve_user(user_id: int, active: bool = True):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if active else 0, user_id))
    conn.commit()
    conn.close()

def reset_password_with_recovery(username: str, answer: str, new_password: str) -> Tuple[bool, str]:
    user = get_user_by_username(username)
    if not user:
        return False, "Usuário não encontrado."
    if not user["recovery_answer_hash"]:
        return False, "Usuário não possui pergunta de recuperação cadastrada."
    if hash_password(answer) != user["recovery_answer_hash"]:
        return False, "Resposta de recuperação incorreta."
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(new_password), user["id"]))
    conn.commit()
    conn.close()
    return True, "Senha alterada com sucesso!"

# ---------- PLANNERS ----------

def get_planners_for_user(user_id: int, is_master: bool = False) -> List[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    if is_master:
        cur.execute("""
        SELECT p.*, u.username as owner_name
        FROM planners p
        JOIN users u ON u.id = p.owner_user_id
        ORDER BY p.created_at DESC
        """)
    else:
        cur.execute("""
        SELECT p.*, u.username as owner_name
        FROM planners p
        JOIN users u ON u.id = p.owner_user_id
        WHERE p.owner_user_id = ?
        ORDER BY p.created_at DESC
        """, (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def create_planner(name: str, planner_type: str, user_id: int, alert_threshold: float = 0.8,
                   currency: str = "R$") -> Tuple[bool, str]:
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    try:
        cur.execute("""
        INSERT INTO planners(name, owner_user_id, type, alert_threshold, currency, created_at)
        VALUES(?,?,?,?,?,?)
        """, (name, user_id, planner_type, alert_threshold, currency, now))
        conn.commit()
        return True, "Planner criado com sucesso!"
    except Exception as e:
        return False, f"Erro ao criar planner: {e}"
    finally:
        conn.close()

def get_planner(planner_id: int) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM planners WHERE id = ?", (planner_id,))
    row = cur.fetchone()
    conn.close()
    return row

# ---------- INCOMES / EXPENSES / CARDS / ADJUSTMENTS ----------

def insert_income(planner_id: int, description: str, income_type: str, amount: float,
                  start_date: date, recurrence: str, months_count: Optional[int]):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
    INSERT INTO incomes(planner_id, description, income_type, amount, start_date, recurrence, months_count, is_active, created_at)
    VALUES(?,?,?,?,?,?,?,?,?)
    """, (planner_id, description, income_type, amount, start_date.isoformat(), recurrence,
          months_count, 1, now))
    conn.commit()
    conn.close()

def get_incomes(planner_id: int) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM incomes WHERE planner_id = ? AND is_active = 1",
                           conn, params=(planner_id,))
    conn.close()
    return df

def delete_income(income_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM incomes WHERE id = ?", (income_id,))
    conn.commit()
    conn.close()

def insert_expense(planner_id: int, description: str, category: str,
                   amount: float, due_date: date):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
    INSERT INTO expenses(planner_id, description, category, amount, due_date, is_paid, created_at)
    VALUES(?,?,?,?,?,?,?)
    """, (planner_id, description, category, amount, due_date.isoformat(), 0, now))
    conn.commit()
    conn.close()

def get_expenses(planner_id: int) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM expenses WHERE planner_id = ?",
                           conn, params=(planner_id,))
    conn.close()
    return df

def delete_expense(expense_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    conn.close()

def set_expense_paid(expense_id: int, paid: bool):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE expenses SET is_paid = ? WHERE id = ?", (1 if paid else 0, expense_id))
    conn.commit()
    conn.close()

def insert_credit_card(planner_id: int, bank_name: str, card_name: str):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
    INSERT INTO credit_cards(planner_id, bank_name, card_name, created_at)
    VALUES(?,?,?,?)
    """, (planner_id, bank_name, card_name, now))
    conn.commit()
    conn.close()

def get_credit_cards(planner_id: int) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query("SELECT * FROM credit_cards WHERE planner_id = ?",
                           conn, params=(planner_id,))
    conn.close()
    return df

def insert_invoice(card_id: int, invoice_month: str, amount_due: float,
                   due_date: date, is_paid: bool):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
    INSERT INTO credit_card_invoices(card_id, invoice_month, amount_due, due_date, is_paid, created_at)
    VALUES(?,?,?,?,?,?)
    """, (card_id, invoice_month, amount_due, due_date.isoformat(), int(is_paid), now))
    conn.commit()
    conn.close()

def get_invoices_for_planner(planner_id: int) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query("""
    SELECT inv.*, c.bank_name, c.card_name
    FROM credit_card_invoices inv
    JOIN credit_cards c ON c.id = inv.card_id
    WHERE c.planner_id = ?
    """, conn, params=(planner_id,))
    conn.close()
    return df

def delete_invoice(invoice_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM credit_card_invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()

def set_invoice_paid(invoice_id: int, paid: bool):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE credit_card_invoices SET is_paid = ? WHERE id = ?", (1 if paid else 0, invoice_id))
    conn.commit()
    conn.close()

def insert_savings_adjustment(planner_id: int, description: str,
                              amount: float, movement_date: date,
                              movement_type: str):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat()
    cur.execute("""
    INSERT INTO savings_adjustments(planner_id, description, amount, movement_date, movement_type, created_at)
    VALUES(?,?,?,?,?,?)
    """, (planner_id, description, amount, movement_date.isoformat(), movement_type, now))
    conn.commit()
    conn.close()

def get_savings_adjustments(planner_id: int) -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM savings_adjustments WHERE planner_id = ?",
        conn,
        params=(planner_id,),
    )
    conn.close()
    return df

def delete_savings_adjustment(adj_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM savings_adjustments WHERE id = ?", (adj_id,))
    conn.commit()
    conn.close()

# ---------- BUSINESS LOGIC ----------

def month_key(dt: date) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"

def month_range(center: date, past: int = 1, future: int = 1) -> List[Tuple[int,int]]:
    months = []
    start_index = -past
    end_index = future
    for i in range(start_index, end_index+1):
        y = center.year + (center.month - 1 + i)//12
        m = (center.month - 1 + i) % 12 + 1
        months.append((y,m))
    return months

def occurs_in_month(start_date: date, recurrence: str,
                    months_count: Optional[int],
                    target_year: int, target_month: int) -> bool:
    months_diff = (target_year - start_date.year)*12 + (target_month - start_date.month)
    if months_diff < 0:
        return False
    if recurrence == "once":
        return months_diff == 0
    if recurrence == "monthly":
        if months_count is None:
            return months_diff >= 0
        return 0 <= months_diff < months_count
    if recurrence == "x_months":
        if months_count is None:
            return False
        return 0 <= months_diff < months_count
    return False

def compute_monthly_income(df_incomes: pd.DataFrame, year: int, month: int) -> float:
    if df_incomes.empty:
        return 0.0
    total = 0.0
    for _, row in df_incomes.iterrows():
        try:
            start_date = datetime.fromisoformat(row["start_date"]).date()
        except Exception:
            continue
        if occurs_in_month(start_date, row["recurrence"], row["months_count"], year, month):
            total += float(row["amount"])
    return total

def compute_monthly_expenses(df_expenses: pd.DataFrame,
                             df_invoices: pd.DataFrame,
                             year: int, month: int) -> float:
    total = 0.0
    if not df_expenses.empty:
        df_e = df_expenses.copy()
        df_e["due_date"] = pd.to_datetime(df_e["due_date"]).dt.date
        mask = (df_e["due_date"].apply(lambda d: d.year == year and d.month == month))
        total += df_e.loc[mask, "amount"].sum()
    if not df_invoices.empty:
        df_i = df_invoices.copy()
        mask2 = df_i["invoice_month"] == f"{year:04d}-{month:02d}"
        total += df_i.loc[mask2, "amount_due"].sum()
    return float(total)

def build_kpi_data(planner_id: int) -> Dict[str, Any]:
    today = date.today()
    df_inc = get_incomes(planner_id)
    df_exp = get_expenses(planner_id)
    df_inv = get_invoices_for_planner(planner_id)

    months = month_range(today, past=1, future=1)
    data = {}
    for y, m in months:
        key = f"{y:04d}-{m:02d}"
        inc = compute_monthly_income(df_inc, y, m)
        exp = compute_monthly_expenses(df_exp, df_inv, y, m)
        data[key] = {"income": inc, "expenses": exp, "net": inc - exp}
    return {
        "raw": data,
        "df_incomes": df_inc,
        "df_expenses": df_exp,
        "df_invoices": df_inv,
        "today": today
    }

def get_due_alerts(planner_id: int, days_ahead: int = 5) -> pd.DataFrame:
    today = date.today()
    limit = today + timedelta(days=days_ahead)
    df_exp = get_expenses(planner_id)
    df_inv = get_invoices_for_planner(planner_id)
    alerts = []

    if not df_exp.empty:
        df_e = df_exp.copy()
        df_e["due_date"] = pd.to_datetime(df_e["due_date"]).dt.date
        if "is_paid" in df_e.columns:
            df_e = df_e[df_e["is_paid"] == 0]
        for _, row in df_e.iterrows():
            d = row["due_date"]
            if today <= d <= limit:
                alerts.append({
                    "tipo": "Despesa",
                    "descricao": row["description"],
                    "categoria": row["category"],
                    "valor": row["amount"],
                    "vencimento": d
                })
    if not df_inv.empty:
        df_i = df_inv.copy()
        df_i["due_date"] = pd.to_datetime(df_i["due_date"]).dt.date
        df_i = df_i[df_i["is_paid"] == 0]
        for _, row in df_i.iterrows():
            d = row["due_date"]
            if today <= d <= limit:
                alerts.append({
                    "tipo": "Cartão de Crédito",
                    "descricao": f"{row['bank_name']} - {row.get('card_name') or 'Cartão'} ({row['invoice_month']})",
                    "categoria": "Fatura",
                    "valor": row["amount_due"],
                    "vencimento": d
                })
    if not alerts:
        return pd.DataFrame(columns=["tipo","descricao","categoria","valor","vencimento"])
    df_alerts = pd.DataFrame(alerts)
    df_alerts.sort_values("vencimento", inplace=True)
    return df_alerts

def compute_accumulated_balances(planner_id: int,
                                 months_past: int = 12,
                                 months_future: int = 12) -> Dict[str, float]:
    """
    saldo_atual: soma dos resultados líquidos dos meses até o mês anterior,
                 ajustado pelos aportes/gastos até hoje.
    saldo_futuro: saldo_atual + projeção dos próximos meses
                  + ajustes futuros.
    """
    today = date.today()
    df_inc = get_incomes(planner_id)
    df_exp = get_expenses(planner_id)
    df_inv = get_invoices_for_planner(planner_id)
    df_adj = get_savings_adjustments(planner_id)

    # grade de meses (12 pra trás, 12 pra frente)
    months = []
    for i in range(-months_past, months_future + 1):
        y = today.year + (today.month - 1 + i) // 12
        m = (today.month - 1 + i) % 12 + 1
        months.append((y, m))

    nets = []
    for y, m in months:
        net = compute_monthly_income(df_inc, y, m) - compute_monthly_expenses(df_exp, df_inv, y, m)
        nets.append({"year": y, "month": m, "net": net})

    def is_past_month(row):
        if row["year"] < today.year:
            return True
        if row["year"] == today.year and row["month"] < today.month:
            return True
        return False

    def is_future_month(row):
        if row["year"] > today.year:
            return True
        if row["year"] == today.year and row["month"] >= today.month:
            return True
        return False

    past_nets = [r["net"] for r in nets if is_past_month(r)]
    future_nets = [r["net"] for r in nets if is_future_month(r)]

    # ajustes
    if df_adj.empty:
        past_adj = 0.0
        future_adj = 0.0
    else:
        df_adj["movement_date"] = pd.to_datetime(df_adj["movement_date"]).dt.date
        df_adj["sign"] = df_adj["movement_type"].apply(lambda t: 1 if t == "aporte" else -1)
        df_adj["eff"] = df_adj["amount"] * df_adj["sign"]

        past_adj = df_adj[df_adj["movement_date"] <= today]["eff"].sum()
        future_adj = df_adj[df_adj["movement_date"] > today]["eff"].sum()

    saldo_atual = float(sum(past_nets) + past_adj)
    saldo_futuro = float(saldo_atual + sum(future_nets) + future_adj)

    return {
        "saldo_atual": saldo_atual,
        "saldo_futuro": saldo_futuro,
    }

# ---------- UI HELPERS ----------

def set_page_config():
    st.set_page_config(
        page_title="Planner Financeiro Inteligente",
        page_icon="💸",
        layout="wide",
    )

    st.markdown("""
    <style>
    .main {
        background: #f5f5f5;
        color: #111827;
    }
    .stApp {
        background-color: transparent;
    }

    .kpi-card {
        border-radius: 16px;
        padding: 14px 18px;
        box-shadow: 0 8px 18px rgba(15,23,42,0.25);
        width: 100%;
        max-width: 320px;
        margin-bottom: 12px;
        color: #f9fafb;
    }
    .kpi-card.kpi-income {
        background: linear-gradient(135deg, #22c55e, #16a34a);
    }
    .kpi-card.kpi-expense {
        background: linear-gradient(135deg, #f97316, #ea580c);
    }
    .kpi-card.kpi-net {
        background: linear-gradient(135deg, #6366f1, #4f46e5);
    }

    .kpi-label {
        font-size: 0.80rem;
        text-transform: uppercase;
        letter-spacing: .08em;
        opacity: 0.9;
    }
    .kpi-value {
        font-size: 1.6rem;
        font-weight: 700;
        margin-top: 4px;
        margin-bottom: 2px;
    }
    .kpi-delta-positive {
        color: #bbf7d0;
        font-size: 0.85rem;
        font-weight: 600;
    }
    .kpi-delta-negative {
        color: #fecaca;
        font-size: 0.85rem;
        font-weight: 600;
    }
    .kpi-sublabel {
        font-size: 0.75rem;
        opacity: 0.95;
    }

    .alert-badge {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 6px 12px;
        border-radius: 999px;
        background: rgba(248,113,113,0.15);
        border: 1px solid rgba(248,113,113,0.6);
        color: #b91c1c;
        font-size: 0.80rem;
        font-weight: 500;
        margin-bottom: 10px;
    }
    .alert-badge span.bell {
        font-size: 1rem;
    }

    /* Alert cards below KPIs */
    .alert-card {
        border-radius: 12px;
        padding: 10px 14px;
        background: #fef2f2;
        border: 1px solid #fecaca;
        margin-bottom: 8px;
    }
    .alert-card-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;
    }
    .alert-icon {
        font-size: 1.1rem;
    }
    .alert-card-title {
        font-weight: 600;
        font-size: 0.95rem;
        color: #991b1b;
    }
    .alert-card-body {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        font-size: 0.85rem;
        margin-bottom: 4px;
    }
    .alert-card-amount {
        font-weight: 700;
        font-size: 1rem;
        color: #b91c1c;
    }
    .alert-card-footer {
        font-size: 0.78rem;
        color: #7f1d1d;
    }
    .alert-card-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 2px 8px;
        border-radius: 999px;
        background: rgba(248,113,113,0.18);
        border: 1px solid rgba(248,113,113,0.5);
    }

    table {
        border-collapse: collapse;
    }
    thead tr th {
        background-color: #111827 !important;
        color: #f9fafb !important;
    }
    tbody tr:nth-child(even) {
        background-color: #e5e7eb !important;
    }
    .stDataFrame, .stDataFrame table {
        color: #111827 !important;
    }
    </style>
    """, unsafe_allow_html=True)

def format_currency(value: float, currency: str = "R$") -> str:
    return f"{currency} {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def show_kpi_card(
    label: str,
    value: float,
    currency: str,
    delta: Optional[float] = None,
    help_text: Optional[str] = None,
    variant: str = "default",
):
    class_name = "kpi-card"
    if variant == "income":
        class_name += " kpi-income"
    elif variant == "expense":
        class_name += " kpi-expense"
    elif variant == "net":
        class_name += " kpi-net"

    parts = [
        f'<div class="{class_name}">',
        f'  <div class="kpi-label">{label}</div>',
        f'  <div class="kpi-value">{format_currency(value, currency)}</div>',
    ]

    if delta is not None:
        cls = "kpi-delta-positive" if delta >= 0 else "kpi-delta-negative"
        symbol = "▲" if delta >= 0 else "▼"
        parts.append(f'  <div class="{cls}">{symbol} {delta:+.1f}% vs mês anterior</div>')

    if help_text:
        parts.append(f'  <div class="kpi-sublabel">{help_text}</div>')

    parts.append("</div>")
    html = "\n".join(parts)
    st.markdown(html, unsafe_allow_html=True)

# ---------- AUTH UI ----------

def login_screen():
    st.title("💸 Planner Financeiro Inteligente")
    st.subheader("Controle total da sua vida financeira, pessoal e empresarial.")

    tab_login, tab_register, tab_recover = st.tabs(["Entrar", "Criar conta", "Recuperar senha"])

    with tab_login:
        username = st.text_input("Usuário", key="login_username")
        password = st.text_input("Senha", type="password", key="login_password")
        if st.button("Entrar", type="primary", use_container_width=True, key="btn_login"):
            user = get_user_by_username(username)
            if not user or not verify_password(password, user["password_hash"]):
                st.error("Usuário ou senha inválidos.")
            elif not user["is_active"]:
                st.warning("Usuário ainda não aprovado pelo master.")
            else:
                st.success(f"Bem-vindo(a), {user['username']}!")
                st.session_state["user"] = {
                    "id": user["id"],
                    "username": user["username"],
                    "is_master": bool(user["is_master"])
                }
                st.session_state["current_planner_id"] = None
                st.rerun()

    with tab_register:
        st.write("Preencha seus dados. Sua conta precisará ser aprovada pelo usuário master.")
        col1, col2 = st.columns(2)
        with col1:
            username_r = st.text_input("Usuário desejado", key="register_username")
            email_r = st.text_input("E-mail", key="register_email")
        with col2:
            pwd_r = st.text_input("Senha", type="password", key="register_password")
            pwd2_r = st.text_input("Confirmar senha", type="password", key="register_password_confirm")
        recovery_question = st.text_input(
            "Pergunta de recuperação (ex: Nome do seu primeiro pet)",
            key="register_recovery_question"
        )
        recovery_answer = st.text_input(
            "Resposta de recuperação",
            type="password",
            key="register_recovery_answer"
        )

        if st.button("Criar conta", use_container_width=True, key="btn_register"):
            if not username_r or not pwd_r:
                st.error("Usuário e senha são obrigatórios.")
            elif pwd_r != pwd2_r:
                st.error("As senhas não conferem.")
            else:
                ok, msg = create_user(username_r, email_r, pwd_r, recovery_question, recovery_answer)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    with tab_recover:
        st.write("Use sua pergunta de recuperação para redefinir a senha.")
        username_rec = st.text_input("Usuário para recuperar", key="recover_username")
        answer_rec = st.text_input("Resposta de recuperação", type="password", key="recover_answer")
        new_pwd = st.text_input("Nova senha", type="password", key="recover_new_password")
        new_pwd2 = st.text_input("Confirmar nova senha", type="password", key="recover_new_password_confirm")

        if st.button("Redefinir senha", use_container_width=True, key="btn_recover"):
            if new_pwd != new_pwd2:
                st.error("As senhas não conferem.")
            else:
                ok, msg = reset_password_with_recovery(username_rec, answer_rec, new_pwd)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

# ---------- MAIN SIDEBAR ----------

def sidebar_planner_selector():
    user = st.session_state["user"]
    planners = get_planners_for_user(user["id"], user["is_master"])
    st.sidebar.markdown("### 📊 Planner atual")

    planner_options = []
    planner_map = {}
    for p in planners:
        label = f"{p['name']} ({'Pessoal' if p['type']=='personal' else 'Empresa'})"
        planner_options.append(label)
        planner_map[label] = p["id"]

    current_planner_id = st.session_state.get("current_planner_id")
    selected_label = None

    if planner_options:
        if current_planner_id:
            for lbl, pid in planner_map.items():
                if pid == current_planner_id:
                    selected_label = lbl
                    break
        selected_label = st.sidebar.selectbox(
            "Selecione um planner",
            planner_options,
            index=planner_options.index(selected_label) if selected_label in planner_options else 0,
            key="planner_selectbox"
        )
        st.session_state["current_planner_id"] = planner_map[selected_label]
    else:
        st.sidebar.info("Você ainda não possui planners. Crie um novo abaixo.")

    with st.sidebar.expander("➕ Criar novo planner"):
        name = st.text_input("Nome do planner", key="planner_name_sidebar")
        planner_type = st.selectbox("Tipo", ["Pessoal", "Empresa"], key="planner_type_sidebar")
        alert_threshold = st.slider(
            "Limite de alerta de despesas / renda", 0.5, 1.0, 0.8, 0.05,
            key="planner_threshold_sidebar"
        )
        currency = st.selectbox("Moeda", ["R$", "US$", "€"], key="planner_currency_sidebar")
        if st.button("Salvar planner", use_container_width=True, key="btn_save_planner_sidebar"):
            if not name:
                st.warning("Informe um nome para o planner.")
            else:
                ok, msg = create_planner(
                    name,
                    "personal" if planner_type == "Pessoal" else "business",
                    user["id"],
                    alert_threshold,
                    currency
                )
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.sidebar.markdown("---")
    if st.sidebar.button("Sair", use_container_width=True, key="btn_logout"):
        for k in ["user", "current_planner_id"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

# ---------- PAGES ----------

def dashboard_page(planner_id: int):
    planner = get_planner(planner_id)
    if not planner:
        st.error("Planner não encontrado.")
        return

    currency = planner["currency"]
    kpi_data = build_kpi_data(planner_id)
    data = kpi_data["raw"]
    today = kpi_data["today"]

    keys_sorted = sorted(data.keys())
    if len(keys_sorted) < 3:
        st.info("Cadastre pelo menos uma renda para visualizar o dashboard completo.")
        return

    prev_key, current_key, next_key = keys_sorted

    inc_prev = data[prev_key]["income"]
    inc_curr = data[current_key]["income"]
    inc_next = data[next_key]["income"]

    exp_prev = data[prev_key]["expenses"]
    exp_curr = data[current_key]["expenses"]
    exp_next = data[next_key]["expenses"]

    renda_delta = ((inc_curr - inc_prev) / inc_prev * 100) if inc_prev else None
    desp_delta = ((exp_curr - exp_prev) / exp_prev * 100) if exp_prev else None

    ratio = (exp_curr / inc_curr) if inc_curr else 0.0
    threshold = planner["alert_threshold"]
    ratio_pct = ratio * 100

    # Saldo acumulado
    acc = compute_accumulated_balances(planner_id)
    saldo_atual = acc["saldo_atual"]
    saldo_futuro = acc["saldo_futuro"]

    st.markdown("## 🌟 Visão geral financeira")
    if ratio > threshold:
        st.markdown(
            f'<div class="alert-badge"><span class="bell">🔔</span> Alerta: suas despesas representam {ratio_pct:.1f}% da renda deste mês (limite configurado: {threshold*100:.0f}%).</div>',
            unsafe_allow_html=True
        )

    col_left, col_right = st.columns([1, 2])

    with col_left:
        show_kpi_card(
            "Renda - mês atual",
            inc_curr,
            currency,
            renda_delta,
            help_text=f"Mês anterior: {format_currency(inc_prev, currency)} • Próximo mês projetado: {format_currency(inc_next, currency)}",
            variant="income",
        )
        show_kpi_card(
            "Despesas - mês atual",
            exp_curr,
            currency,
            desp_delta,
            help_text=f"Mês anterior: {format_currency(exp_prev, currency)} • Próximo mês previsto: {format_currency(exp_next, currency)}",
            variant="expense",
        )
        show_kpi_card(
            "Resultado do mês (renda - despesas)",
            inc_curr - exp_curr,
            currency,
            None,
            help_text=f"Comprometimento: {ratio_pct:.1f}% da renda",
            variant="net",
        )
        show_kpi_card(
            "Saldo acumulado até o mês atual",
            saldo_atual,
            currency,
            None,
            help_text="Resultado histórico ajustado por aportes e gastos pontuais.",
            variant="net",
        )
        show_kpi_card(
            "Saldo acumulado projetado (12 meses)",
            saldo_futuro,
            currency,
            None,
            help_text="Projeção considerando rendas, despesas e ajustes futuros.",
            variant="net",
        )

        # ---- Alertas em cards, logo abaixo dos KPIs ----
        st.markdown("### 🔔 Contas próximas do vencimento")
        alerts_df = get_due_alerts(planner_id, days_ahead=5)
        if alerts_df.empty:
            st.success("Nenhuma conta vencendo nos próximos 5 dias. 🎉")
        else:
            today_local = date.today()
            for _, row in alerts_df.iterrows():
                venc = row["vencimento"]
                if not isinstance(venc, date):
                    try:
                        venc = pd.to_datetime(venc).date()
                    except Exception:
                        venc = None
                if venc:
                    dias = (venc - today_local).days
                    if dias < 0:
                        status_text = "Já vencida"
                    elif dias == 0:
                        status_text = "Vence hoje"
                    elif dias == 1:
                        status_text = "Vence amanhã"
                    else:
                        status_text = f"Vence em {dias} dias"
                    venc_str = venc.strftime("%d/%m/%Y")
                else:
                    status_text = ""
                    venc_str = str(row["vencimento"])

                valor_str = format_currency(float(row["valor"]), currency)
                desc = row["descricao"]
                tipo = row.get("tipo", "")
                categoria = row.get("categoria", "")

                card_html = f"""
                <div class="alert-card">
                    <div class="alert-card-header">
                        <span class="alert-icon">🔔</span>
                        <div class="alert-card-title">{desc}</div>
                    </div>
                    <div class="alert-card-body">
                        <div class="alert-card-meta">
                            <strong>Vencimento:</strong> {venc_str}<br/>
                            <strong>Tipo:</strong> {tipo} • {categoria}
                        </div>
                        <div class="alert-card-amount">
                            {valor_str}
                        </div>
                    </div>
                    <div class="alert-card-footer">
                        <span class="alert-card-badge">⚠️ Conta vencendo • {status_text}</span>
                    </div>
                </div>
                """
                st.markdown(card_html, unsafe_allow_html=True)

    with col_right:
        st.markdown("### 📈 Tendência de renda, despesas e resultado")

        # Monta séries com rótulos no formato MM/AAAA
        months_labels = [f"{k.split('-')[1]}/{k.split('-')[0]}" for k in keys_sorted]
        incomes_vals = [data[k]["income"] for k in keys_sorted]
        expenses_vals = [data[k]["expenses"] for k in keys_sorted]
        net_vals = [data[k]["net"] for k in keys_sorted]

        fig = go.Figure()

        fig.add_bar(
            name="Renda",
            x=months_labels,
            y=incomes_vals,
            text=[format_currency(v, currency) for v in incomes_vals],
            textposition="outside",
        )
        fig.add_bar(
            name="Despesas",
            x=months_labels,
            y=expenses_vals,
            text=[format_currency(v, currency) for v in expenses_vals],
            textposition="outside",
        )

        fig.add_trace(
            go.Scatter(
                name="Resultado",
                x=months_labels,
                y=net_vals,
                mode="lines+markers+text",
                text=[format_currency(v, currency) for v in net_vals],
                textposition="top center",
                yaxis="y2",
            )
        )

        fig.update_layout(
            barmode="group",
            height=420,
            margin=dict(l=10, r=50, t=60, b=40),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
            ),
            yaxis=dict(
                title=f"Valores ({currency})",
                rangemode="tozero",
            ),
            yaxis2=dict(
                title="Resultado",
                overlaying="y",
                side="right",
                showgrid=False,
            ),
        )

        st.plotly_chart(fig, use_container_width=True)

        st.markdown("### 🧩 Composição das despesas do mês atual")
        df_exp = kpi_data["df_expenses"]
        df_inv = kpi_data["df_invoices"]
        year = today.year
        month = today.month

        parts = []
        if not df_exp.empty:
            df_e = df_exp.copy()
            df_e["due_date"] = pd.to_datetime(df_e["due_date"]).dt.date
            df_e = df_e[df_e["due_date"].apply(lambda d: d.year == year and d.month == month)]
            if not df_e.empty:
                grp = df_e.groupby("category")["amount"].sum().reset_index()
                grp["tipo"] = "Despesas"
                parts.append(grp)
        if not df_inv.empty:
            df_i = df_inv.copy()
            df_i = df_i[df_i["invoice_month"] == f"{year:04d}-{month:02d}"]
            if not df_i.empty:
                grp2 = df_i.groupby("bank_name")["amount_due"].sum().reset_index()
                grp2.rename(columns={"bank_name": "category", "amount_due": "amount"}, inplace=True)
                grp2["tipo"] = "Cartões"
                parts.append(grp2)
        if parts:
            df_pie = pd.concat(parts, ignore_index=True)
            df_pie["label"] = df_pie["tipo"] + " - " + df_pie["category"]
            fig2 = px.pie(df_pie, names="label", values="amount", hole=0.45)
            fig2.update_layout(height=350, margin=dict(l=10,r=10,t=40,b=10))
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Ainda não há dados de despesas para o mês atual.")

def incomes_page(planner_id: int):
    st.header("💰 Rendas")
    planner = get_planner(planner_id)
    currency = planner["currency"]

    tab_new, tab_list = st.tabs(["Cadastrar renda", "Listar / excluir rendas"])

    with tab_new:
        col1, col2 = st.columns(2)
        with col1:
            desc = st.text_input("Descrição da renda (ex: Salário, Comissão)", key="income_desc")
            income_type = st.selectbox(
                "Tipo de renda",
                ["Fixa", "Comissão", "Premiação", "Extra", "Outros"],
                key="income_type"
            )
            amount = st.number_input(
                f"Valor ({currency})",
                min_value=0.0,
                step=100.0,
                format="%.2f",
                key="income_amount"
            )
        with col2:
            start_date = st.date_input("Data inicial", value=date.today(), key="income_start_date")
            recurrence = st.selectbox(
                "Recorrência",
                ["Apenas este mês", "Todos os meses", "Por número de meses"],
                key="income_recurrence"
            )
            months_count = None
            if recurrence == "Por número de meses":
                months_count = st.number_input(
                    "Quantidade de meses",
                    min_value=1,
                    step=1,
                    value=1,
                    key="income_months_count"
                )

        if st.button("Salvar renda", type="primary", key="btn_save_income"):
            if not desc or amount <= 0:
                st.error("Informe descrição e valor positivo.")
            else:
                rec_value = "once"
                if recurrence == "Todos os meses":
                    rec_value = "monthly"
                elif recurrence == "Por número de meses":
                    rec_value = "x_months"
                insert_income(
                    planner_id,
                    desc,
                    income_type,
                    amount,
                    start_date,
                    rec_value,
                    int(months_count) if months_count else None
                )
                st.success("Renda cadastrada com sucesso!")

    with tab_list:
        df = get_incomes(planner_id)
        if df.empty:
            st.info("Nenhuma renda cadastrada ainda.")
        else:
            df_view = df.copy()
            df_view["start_date"] = pd.to_datetime(df_view["start_date"]).dt.date
            df_view.rename(columns={
                "description": "Descrição",
                "income_type": "Tipo",
                "amount": "Valor",
                "start_date": "Data inicial",
                "recurrence": "Recorrência",
                "months_count": "Qtd meses"
            }, inplace=True)
            st.dataframe(
                df_view[["id","Descrição","Tipo","Valor","Data inicial","Recorrência","Qtd meses"]],
                use_container_width=True
            )

            ids = df["id"].tolist()
            id_to_delete = st.selectbox(
                "Selecione uma renda para excluir",
                options=[""]+ids,
                key="income_delete_select"
            )
            if id_to_delete:
                if st.button("Excluir renda selecionada", key="btn_delete_income"):
                    delete_income(int(id_to_delete))
                    st.success("Renda excluída.")
                    st.rerun()

def expenses_page(planner_id: int):
    st.header("💸 Despesas")
    planner = get_planner(planner_id)
    currency = planner["currency"]

    categories = [
        "Financiamento", "Luz", "Água", "Internet", "Cartão de Crédito (compra)",
        "Telefonia", "Aluguel", "Impostos", "Outros"
    ]

    tab_new, tab_list = st.tabs(["Cadastrar despesa", "Listar / controlar despesas"])

    with tab_new:
        col1, col2 = st.columns(2)
        with col1:
            desc = st.text_input("Descrição da despesa", key="expense_desc")
            category = st.selectbox("Categoria", categories, key="expense_category")
            amount = st.number_input(
                f"Valor ({currency})",
                min_value=0.0,
                step=50.0,
                format="%.2f",
                key="expense_amount"
            )
        with col2:
            due_date = st.date_input(
                "Data de vencimento",
                value=date.today(),
                key="expense_due_date"
            )
            st.caption("Para despesas futuras, selecione a data de vencimento correspondente.")

        if st.button("Salvar despesa", type="primary", key="btn_save_expense"):
            if not desc or amount <= 0:
                st.error("Informe descrição e valor positivo.")
            else:
                insert_expense(planner_id, desc, category, amount, due_date)
                st.success("Despesa cadastrada com sucesso!")

    with tab_list:
        df = get_expenses(planner_id)
        if df.empty:
            st.info("Nenhuma despesa cadastrada ainda.")
        else:
            df_view = df.copy()
            df_view["due_date"] = pd.to_datetime(df_view["due_date"]).dt.date
            if "is_paid" not in df_view.columns:
                df_view["is_paid"] = 0
            df_view["Status"] = df_view["is_paid"].apply(lambda v: "Paga" if v else "Pendente")
            df_view.rename(columns={
                "description": "Descrição",
                "category": "Categoria",
                "amount": "Valor",
                "due_date": "Vencimento"
            }, inplace=True)
            st.dataframe(
                df_view[["id","Descrição","Categoria","Valor","Vencimento","Status"]],
                use_container_width=True
            )

            st.markdown("#### Manutenção de despesas")

            ids = df["id"].tolist()
            id_selected = st.selectbox(
                "Selecione uma despesa",
                options=[""] + ids,
                key="expense_select_maintenance"
            )

            if id_selected:
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if st.button("Excluir despesa selecionada", key="btn_delete_expense"):
                        delete_expense(int(id_selected))
                        st.success("Despesa excluída.")
                        st.rerun()
                with col_b:
                    novo_status = st.selectbox(
                        "Status de pagamento",
                        ["Pendente", "Paga"],
                        key="expense_status_select"
                    )
                with col_c:
                    if st.button("Atualizar status", key="btn_update_expense_status"):
                        set_expense_paid(int(id_selected), novo_status == "Paga")
                        st.success("Status atualizado.")
                        st.rerun()

def credit_cards_page(planner_id: int):
    st.header("💳 Cartões de crédito")
    planner = get_planner(planner_id)
    currency = planner["currency"]

    tab_cards, tab_invoices = st.tabs(["Cadastrar cartões", "Faturas"])

    with tab_cards:
        st.subheader("Cadastro de cartões")
        col1, col2 = st.columns(2)
        with col1:
            bank_name = st.text_input("Banco / emissor do cartão", key="card_bank_name")
        with col2:
            card_name = st.text_input("Nome / apelido do cartão (opcional)", key="card_name")
        if st.button("Salvar cartão", type="primary", key="btn_save_card"):
            if not bank_name:
                st.error("Informe o banco / emissor.")
            else:
                insert_credit_card(planner_id, bank_name, card_name)
                st.success("Cartão cadastrado com sucesso!")

        st.markdown("#### Cartões cadastrados")
        df_cards = get_credit_cards(planner_id)
        if df_cards.empty:
            st.info("Nenhum cartão cadastrado ainda.")
        else:
            df_view = df_cards.copy()
            df_view.rename(columns={
                "id": "ID",
                "bank_name": "Banco",
                "card_name": "Nome do cartão"
            }, inplace=True)
            st.dataframe(df_view[["ID","Banco","Nome do cartão"]], use_container_width=True)

    with tab_invoices:
        st.subheader("Cadastro de faturas")
        df_cards = get_credit_cards(planner_id)
        if df_cards.empty:
            st.info("Cadastre ao menos um cartão na aba anterior.")
        else:
            card_options = {
                f"{row['bank_name']} - {row['card_name'] or 'Cartão'} (ID {row['id']})": row["id"]
                for _, row in df_cards.iterrows()
            }
            card_label = st.selectbox(
                "Selecione o cartão",
                list(card_options.keys()),
                key="invoice_card_select"
            )
            card_id = card_options[card_label]
            col1, col2, col3 = st.columns(3)
            with col1:
                invoice_month = st.text_input(
                    "Mês da fatura (AAAA-MM)",
                    value=month_key(date.today()),
                    key="invoice_month"
                )
            with col2:
                amount_due = st.number_input(
                    f"Valor da fatura ({currency})",
                    min_value=0.0,
                    step=50.0,
                    format="%.2f",
                    key="invoice_amount"
                )
            with col3:
                due_date = st.date_input(
                    "Vencimento da fatura",
                    value=date.today(),
                    key="invoice_due_date"
                )
            is_paid = st.checkbox("Fatura já está paga?", value=False, key="invoice_is_paid")

            if st.button("Salvar fatura", type="primary", key="btn_save_invoice"):
                if not invoice_month or amount_due <= 0:
                    st.error("Informe mês e valor da fatura.")
                else:
                    insert_invoice(card_id, invoice_month, amount_due, due_date, is_paid)
                    st.success("Fatura cadastrada com sucesso!")

        st.markdown("#### Faturas cadastradas")
        df_inv = get_invoices_for_planner(planner_id)
        if df_inv.empty:
            st.info("Nenhuma fatura cadastrada ainda.")
        else:
            df_view = df_inv.copy()
            df_view["due_date"] = pd.to_datetime(df_view["due_date"]).dt.date
            df_view["Status"] = df_view["is_paid"].apply(lambda v: "Paga" if v else "Em aberto")
            df_view.rename(columns={
                "id": "ID",
                "bank_name": "Banco",
                "card_name": "Cartão",
                "invoice_month": "Mês ref.",
                "amount_due": "Valor",
                "due_date": "Vencimento",
            }, inplace=True)
            st.dataframe(
                df_view[["ID","Banco","Cartão","Mês ref.","Valor","Vencimento","Status"]],
                use_container_width=True
            )

            st.markdown("#### Manutenção de faturas")
            ids = df_inv["id"].tolist()
            inv_selected = st.selectbox(
                "Selecione uma fatura",
                options=[""] + ids,
                key="invoice_select_maintenance"
            )
            if inv_selected:
                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if st.button("Excluir fatura selecionada", key="btn_delete_invoice"):
                        delete_invoice(int(inv_selected))
                        st.success("Fatura excluída.")
                        st.rerun()
                with col_b:
                    novo_status = st.selectbox(
                        "Status da fatura",
                        ["Em aberto", "Paga"],
                        key="invoice_status_select"
                    )
                with col_c:
                    if st.button("Atualizar status da fatura", key="btn_update_invoice_status"):
                        set_invoice_paid(int(inv_selected), novo_status == "Paga")
                        st.success("Status atualizado.")
                        st.rerun()

def alerts_page(planner_id: int):
    st.header("🔔 Alertas inteligentes (visão detalhada)")

    st.markdown("### Contas próximas do vencimento (próximos 5 dias)")
    df_alerts_5 = get_due_alerts(planner_id, days_ahead=5)
    if df_alerts_5.empty:
        st.success("Nenhuma conta vencendo nos próximos 5 dias. 🎉")
    else:
        st.dataframe(df_alerts_5, use_container_width=True)

    st.markdown("### Contas vencendo amanhã")
    df_alerts_1 = get_due_alerts(planner_id, days_ahead=1)
    if df_alerts_1.empty:
        st.info("Nenhuma conta vencendo amanhã.")
    else:
        st.dataframe(df_alerts_1, use_container_width=True)

def savings_adjustments_page(planner_id: int):
    st.header("💼 Saldo acumulado & gastos pontuais")

    planner = get_planner(planner_id)
    currency = planner["currency"]

    acc = compute_accumulated_balances(planner_id)
    st.subheader("Visão rápida do saldo")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Saldo acumulado até o mês atual", format_currency(acc["saldo_atual"], currency))
    with col2:
        st.metric("Saldo acumulado projetado (12 meses)", format_currency(acc["saldo_futuro"], currency))

    st.markdown("---")
    st.subheader("Novo ajuste de saldo")

    col1, col2, col3 = st.columns(3)
    with col1:
        desc = st.text_input("Descrição", key="adj_desc", placeholder="Ex: Compra de livro, viagem, aporte extra")
    with col2:
        movement_date = st.date_input("Data do movimento", value=date.today(), key="adj_date")
    with col3:
        movement_type = st.selectbox(
            "Tipo de movimento",
            ["Gasto pontual (usa o saldo)", "Aporte ao saldo"],
            key="adj_type"
        )

    amount = st.number_input(
        f"Valor ({currency})",
        min_value=0.0,
        step=50.0,
        format="%.2f",
        key="adj_amount"
    )

    if st.button("Registrar ajuste", type="primary", key="btn_save_adj"):
        if not desc or amount <= 0:
            st.error("Informe descrição e valor positivo.")
        else:
            mt = "gasto" if "Gasto" in movement_type else "aporte"
            insert_savings_adjustment(planner_id, desc, amount, movement_date, mt)
            st.success("Ajuste registrado com sucesso!")
            st.rerun()

    st.markdown("### Histórico de ajustes")
    df_adj = get_savings_adjustments(planner_id)
    if df_adj.empty:
        st.info("Nenhum ajuste registrado ainda.")
    else:
        df_view = df_adj.copy()
        df_view["movement_date"] = pd.to_datetime(df_view["movement_date"]).dt.date
        df_view["Efeito"] = df_view["movement_type"].apply(
            lambda t: "Aumenta saldo" if t == "aporte" else "Reduz saldo"
        )
        df_view.rename(columns={
            "id": "ID",
            "description": "Descrição",
            "amount": "Valor",
            "movement_date": "Data",
            "movement_type": "Tipo",
        }, inplace=True)
        st.dataframe(
            df_view[["ID","Data","Descrição","Tipo","Efeito","Valor"]],
            use_container_width=True
        )

        ids = df_adj["id"].tolist()
        id_to_delete = st.selectbox(
            "Selecione um ajuste para excluir",
            options=[""] + ids,
            key="adj_delete_select"
        )
        if id_to_delete:
            if st.button("Excluir ajuste selecionado", key="btn_delete_adj"):
                delete_savings_adjustment(int(id_to_delete))
                st.success("Ajuste excluído.")
                st.rerun()

def admin_page():
    st.header("🛠 Administração (Master)")
    st.write("Aprovação de usuários e visão geral dos planners.")

    conn = get_connection()
    df_users = pd.read_sql_query(
        "SELECT id, username, email, is_active, is_master, created_at FROM users",
        conn
    )
    conn.close()
    if df_users.empty:
        st.info("Nenhum usuário encontrado.")
    else:
        st.subheader("Usuários")
        st.dataframe(df_users, use_container_width=True)

        ids = df_users["id"].tolist()
        user_id = st.selectbox(
            "Selecione um usuário para aprovar / desativar",
            options=[""]+ids,
            key="admin_user_select"
        )
        if user_id:
            status = st.radio(
                "Status desejado",
                ["Ativo", "Inativo"],
                horizontal=True,
                key="admin_user_status"
            )
            if st.button("Atualizar status do usuário", key="btn_admin_update_user"):
                approve_user(int(user_id), active=(status == "Ativo"))
                st.success("Status atualizado.")
                st.rerun()

    st.subheader("Planners")
    conn = get_connection()
    df_planners = pd.read_sql_query("""
    SELECT p.id, p.name, p.type, p.alert_threshold, p.currency, p.created_at,
           u.username as owner
    FROM planners p
    JOIN users u ON u.id = p.owner_user_id
    """, conn)
    conn.close()
    if df_planners.empty:
        st.info("Nenhum planner cadastrado.")
    else:
        st.dataframe(df_planners, use_container_width=True)

# ---------- MAIN ----------

def main_app():
    set_page_config()
    init_db()

    if "user" not in st.session_state:
        login_screen()
        return

    sidebar_planner_selector()
    planner_id = st.session_state.get("current_planner_id")
    if not planner_id:
        st.info("Selecione ou crie um planner para começar.")
        return

    user = st.session_state["user"]
    menu = ["Dashboard", "Rendas", "Despesas", "Cartões de crédito", "Alertas", "Saldo acumulado"]
    if user["is_master"]:
        menu.append("Administração")

    choice = st.sidebar.radio("Navegação", menu, key="main_menu")

    if choice == "Dashboard":
        dashboard_page(planner_id)
    elif choice == "Rendas":
        incomes_page(planner_id)
    elif choice == "Despesas":
        expenses_page(planner_id)
    elif choice == "Cartões de crédito":
        credit_cards_page(planner_id)
    elif choice == "Alertas":
        alerts_page(planner_id)
    elif choice == "Saldo acumulado":
        savings_adjustments_page(planner_id)
    elif choice == "Administração" and user["is_master"]:
        admin_page()

if __name__ == "__main__":
    main_app()
