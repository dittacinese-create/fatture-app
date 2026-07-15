import os
import urllib.parse as urlparse
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, flash, g

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chiave-segreta-temporanea")
DATABASE_URL = os.environ.get("DATABASE_URL")

# ==========================================
# GESTIONE DATABASE (PostgreSQL)
# ==========================================

def get_db():
    """Apre una nuova connessione al database PostgreSQL se non esiste già."""
    db = getattr(g, '_database', None)
    if db is None:
        # Gestisce la connessione tramite la stringa DATABASE_URL di Render
        db = g._database = psycopg2.connect(DATABASE_URL, sslmode='require')
    return db

@app.teardown_appcontext
def close_connection(exception):
    """Chiude automaticamente la connessione al database a fine richiesta."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Inizializza il database creando le tabelle se non esistono (Sintassi PostgreSQL)."""
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        
        # Tabella Clienti
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clienti (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                email TEXT,
                telefono TEXT
            )
        """)
        
        # Tabella Fatture
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fatture (
                id SERIAL PRIMARY KEY,
                numero TEXT NOT NULL,
                data TEXT,
                data_scadenza TEXT,
                cliente_id INTEGER,
                cliente_nome TEXT,
                tipo TEXT CHECK(tipo IN ('FORNITURA', 'MANUALE')) DEFAULT 'MANUALE',
                totale REAL DEFAULT 0.0,
                note TEXT,
                stato_pagamento TEXT CHECK(stato_pagamento IN ('Non pagata', 'In attesa', 'Pagata')) DEFAULT 'Non pagata',
                stato TEXT CHECK(stato IN ('BOZZA', 'CHIUSA')) DEFAULT 'BOZZA',
                FOREIGN KEY (cliente_id) REFERENCES clienti(id)
            )
        """)
        
        # Tabella Prodotti
        cur.execute("""
            CREATE TABLE IF NOT EXISTS prodotti (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                prezzo REAL DEFAULT 0.0
            )
        """)
        
        # Tabella Note
        cur.execute("""
            CREATE TABLE IF NOT EXISTS note (
                id SERIAL PRIMARY KEY,
                titolo TEXT NOT NULL,
                contenuto TEXT,
                data_creazione TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        db.commit()
        cur.close()

if DATABASE_URL:
    init_db()


# ==========================================
# ROTTE DELL'APPLICAZIONE
# ==========================================

@app.route("/")
def index():
    return redirect(url_for("fatture"))


# --- SEZIONE FATTURE ---

@app.route("/fatture")
def fatture():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM fatture ORDER BY data DESC, numero DESC")
    elenco_fatture = cur.fetchall()
    cur.close()
    return render_template("fatture.html", fatture=elenco_fatture)


@app.route("/nuova_fattura", methods=["GET", "POST"])
def nuova_fattura():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    if request.method == "POST":
        numero = request.form.get("numero")
        data = request.form.get("data")
        data_scadenza = request.form.get("data_scadenza")
        cliente_id = request.form.get("cliente_id")
        tipo = request.form.get("tipo", "MANUALE")
        totale = request.form.get("totale", 0.0)
        note = request.form.get("note")
        stato_pagamento = request.form.get("stato_pagamento", "Non pagata")
        stato = request.form.get("stato", "BOZZA")
        
        cur.execute("SELECT nome FROM clienti WHERE id = %s", (cliente_id,))
        cliente = cur.fetchone()
        cliente_nome = cliente["nome"] if cliente else "Cliente Generico"
        
        cur.execute("""
            INSERT INTO fatture (numero, data, data_scadenza, cliente_id, cliente_nome, tipo, totale, note, stato_pagamento, stato)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (numero, data, data_scadenza, cliente_id, cliente_nome, tipo, totale, note, stato_pagamento, stato))
        
        db.commit()
        cur.close()
        flash("Fattura creata con successo!", "success")
        return redirect(url_for("fatture"))
        
    cur.execute("SELECT id, nome FROM clienti ORDER BY nome ASC")
    clienti = cur.fetchall()
    cur.close()
    return render_template("nuova_fattura.html", clienti=clienti)


@app.route("/fattura/<int:fattura_id>")
def vedi_fattura(fattura_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    cur.close()
    if not f:
        flash("Fattura non trovata.", "danger")
        return redirect(url_for("fatture"))
    return render_template("fattura_dettaglio.html", f=f)


@app.route("/delete_fattura/<int:fattura_id>")
def delete_fattura(fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM fatture WHERE id = %s", (fattura_id,))
    db.commit()
    cur.close()
    flash("Fattura eliminata con successo.", "success")
    return redirect(url_for("fatture"))


# --- DASHBOARD ANALISI ---

@app.route("/dashboard")
def dashboard():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # 1. KPI Monetari
    cur.execute("""
        SELECT 
            COALESCE(SUM(totale), 0) as totale_fatturato,
            COALESCE(SUM(CASE WHEN stato_pagamento = 'Pagata' THEN totale ELSE 0 END), 0) as totale_incassato,
            COALESCE(SUM(CASE WHEN stato_pagamento = 'In attesa' THEN totale ELSE 0 END), 0) as totale_attesa,
            COALESCE(SUM(CASE WHEN stato_pagamento = 'Non pagata' OR stato_pagamento IS NULL THEN totale ELSE 0 END), 0) as totale_non_pagato
        FROM fatture
        WHERE stato = 'CHIUSA'
    """)
    stats = cur.fetchone()

    # 2. Conteggio documenti
    cur.execute("""
        SELECT 
            COUNT(*) as totale_invii,
            COUNT(CASE WHEN stato = 'BOZZA' THEN 1 END) as bozze,
            COUNT(CASE WHEN stato = 'CHIUSA' THEN 1 END) as chiuse
        FROM fatture
    """)
    conteggi = cur.fetchone()

    # 3. Andamento mensile ultimi 6 mesi (Adattato a PostgreSQL usando SUBSTRING)
    cur.execute("""
        SELECT 
            SUBSTRING(data FROM 1 FOR 7) as mese,
            COALESCE(SUM(totale), 0) as totale
        FROM fatture
        WHERE stato = 'CHIUSA' AND data IS NOT NULL AND data != ''
        GROUP BY SUBSTRING(data FROM 1 FOR 7)
        ORDER BY mese DESC
        LIMIT 6
    """)
    trend_mensile = cur.fetchall()
    trend_mensile = trend_mensile[::-1]

    # 4. Top 5 Clienti
    cur.execute("""
        SELECT 
            cliente_nome,
            COALESCE(SUM(totale), 0) as totale
        FROM fatture
        WHERE stato = 'CHIUSA'
        GROUP BY cliente_id, cliente_nome
        ORDER BY totale DESC
        LIMIT 5
    """)
    top_clienti = cur.fetchall()
    cur.close()

    return render_template(
        "dashboard.html",
        stats=stats,
        conteggi=conteggi,
        trend_mensile=trend_mensile,
        top_clienti=top_clienti
    )


# --- SEZIONE CLIENTI, PRODOTTI, NOTE e LOGOUT ---

@app.route("/clienti")
def clienti():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM clienti ORDER BY nome ASC")
    elenco_clienti = cur.fetchall()
    cur.close()
    return render_template("clienti.html", clienti=elenco_clienti)


@app.route("/prodotti")
def prodotti():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM prodotti ORDER BY nome ASC")
    elenco_prodotti = cur.fetchall()
    cur.close()
    return render_template("prodotti.html", prodotti=elenco_prodotti)


@app.route("/note")
def note():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM note ORDER BY data_creazione DESC")
    elenco_note = cur.fetchall()
    cur.close()
    return render_template("note.html", note=elenco_note)


@app.route("/logout")
def logout():
    flash("Disconnessione effettuata con successo.", "info")
    return redirect(url_for("fatture"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)