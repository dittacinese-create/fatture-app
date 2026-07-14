import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, g

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chiave-segreta-temporanea")
DATABASE = os.environ.get("DATABASE_URL", "fatture.db")

# ==========================================
# GESTIONE DATABASE (SQLite)
# ==========================================

def get_db():
    """Apre una nuova connessione al database se non esiste già per questa richiesta."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        # Consente di accedere alle colonne per nome (es. riga['cliente_nome'])
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    """Chiude automaticamente la connessione al database a fine richiesta."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Inizializza il database creando le tabelle se non esistono."""
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        
        # Tabella Clienti
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clienti (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT,
                telefono TEXT
            )
        """)
        
        # Tabella Fatture
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fatture (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                prezzo REAL DEFAULT 0.0
            )
        """)
        
        # Tabella Note
        cur.execute("""
            CREATE TABLE IF NOT EXISTS note (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titolo TEXT NOT NULL,
                contenuto TEXT,
                data_creazione TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        db.commit()

# Inizializza le tabelle all'avvio dell'applicazione
init_db()


# ==========================================
# ROTTE DELL'APPLICAZIONE
# ==========================================

# Home / Reindirizzamento alle fatture
@app.route("/")
def index():
    return redirect(url_for("fatture"))


# --- SEZIONE FATTURE ---

@app.route("/fatture")
def fatture():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM fatture ORDER BY data DESC, numero DESC")
    elenco_fatture = cur.fetchall()
    return render_template("fatture.html", fatture=elenco_fatture)


@app.route("/nuova_fattura", methods=["GET", "POST"])
def nuova_fattura():
    db = get_db()
    cur = db.cursor()
    
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
        
        # Recuperiamo il nome del cliente per salvarlo denormalizzato nella fattura
        cur.execute("SELECT nome FROM clienti WHERE id = ?", (cliente_id,))
        cliente = cur.fetchone()
        cliente_nome = cliente["nome"] if cliente else "Cliente Generico"
        
        cur.execute("""
            INSERT INTO fatture (numero, data, data_scadenza, cliente_id, cliente_nome, tipo, totale, note, stato_pagamento, stato)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (numero, data, data_scadenza, cliente_id, cliente_nome, tipo, totale, note, stato_pagamento, stato))
        
        db.commit()
        flash("Fattura creata con successo!", "success")
        return redirect(url_for("fatture"))
        
    cur.execute("SELECT id, nome FROM clienti ORDER BY nome ASC")
    clienti = cur.fetchall()
    return render_template("nuova_fattura.html", clienti=clienti)


@app.route("/fattura/<int:fattura_id>")
def vedi_fattura(fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM fatture WHERE id = ?", (fattura_id,))
    f = cur.fetchone()
    if not f:
        flash("Fattura non trovata.", "danger")
        return redirect(url_for("fatture"))
    return render_template("fattura_dettaglio.html", f=f)


@app.route("/delete_fattura/<int:fattura_id>")
def delete_fattura(fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM fatture WHERE id = ?", (fattura_id,))
    db.commit()
    flash("Fattura eliminata con successo.", "success")
    return redirect(url_for("fatture"))


# --- NUOVA SEZIONE: DASHBOARD ANALISI ---

@app.route("/dashboard")
def dashboard():
    db = get_db()
    cur = db.cursor()
    
    # 1. KPI Monetari (Totale, Incassato, In attesa, Non pagato)
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

    # 2. Conteggio documenti (Bozze vs Chiuse)
    cur.execute("""
        SELECT 
            COUNT(*) as totale_invii,
            COUNT(CASE WHEN stato = 'BOZZA' THEN 1 END) as bozze,
            COUNT(CASE WHEN stato = 'CHIUSA' THEN 1 END) as chiuse
        FROM fatture
    """)
    conteggi = cur.fetchone()

    # 3. Andamento mensile ultimi 6 mesi (SQLite SUBSTR)
    cur.execute("""
        SELECT 
            SUBSTR(data, 1, 7) as mese,
            COALESCE(SUM(totale), 0) as totale
        FROM fatture
        WHERE stato = 'CHIUSA' AND data IS NOT NULL AND data != ''
        GROUP BY SUBSTR(data, 1, 7)
        ORDER BY mese DESC
        LIMIT 6
    """)
    trend_mensile = cur.fetchall()
    trend_mensile = trend_mensile[::-1] # Inverte l'ordine per avere la sequenza temporale corretta

    # 4. Top 5 Clienti per volume d'affari
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

    return render_template(
        "dashboard.html",
        stats=stats,
        conteggi=conteggi,
        trend_mensile=trend_mensile,
        top_clienti=top_clienti
    )


# --- SEZIONE CLIENTI ---

@app.route("/clienti")
def clienti():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM clienti ORDER BY nome ASC")
    elenco_clienti = cur.fetchall()
    return render_template("clienti.html", clienti=elenco_clienti)


# --- SEZIONE PRODOTTI ---

@app.route("/prodotti")
def prodotti():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM prodotti ORDER BY nome ASC")
    elenco_prodotti = cur.fetchall()
    return render_template("prodotti.html", prodotti=elenco_prodotti)


# --- SEZIONE NOTE ---

@app.route("/note")
def note():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM note ORDER BY data_creazione DESC")
    elenco_note = cur.fetchall()
    return render_template("note.html", note=elenco_note)


# --- LOGOUT ---

@app.route("/logout")
def logout():
    # Gestione fittizia o reset di eventuale sessione esistente
    flash("Disconnessione effettuata con successo.", "info")
    return redirect(url_for("fatture"))


# ==========================================
# AVVIO APP
# ==========================================

if __name__ == "__main__":
    # Su Render viene utilizzata la variabile PORT definita dall'ambiente
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)