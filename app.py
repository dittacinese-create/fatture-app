import os
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, flash, g, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chiave-segreta-temporanea")
DATABASE_URL = os.environ.get("DATABASE_URL")

# ==========================================
# GESTIONE DATABASE (PostgreSQL)
# ==========================================

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = psycopg2.connect(DATABASE_URL, sslmode='require')
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cur = db.cursor()
        
        # Tabella Clienti
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clienti (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                indirizzo TEXT,
                partita_iva TEXT,
                codice_fiscale TEXT,
                codice_sdi TEXT,
                pec TEXT
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
                totale REAL DEFAULT 0.0,
                note TEXT,
                stato_pagamento TEXT DEFAULT 'Non pagata',
                stato TEXT DEFAULT 'BOZZA',
                FOREIGN KEY (cliente_id) REFERENCES clienti(id) ON DELETE SET NULL
            )
        """)
        db.commit()

        # Migrazioni per colonne addizionali strutturate
        colonne_da_aggiungere = [
            ("regime_iva", "TEXT DEFAULT '22'"),
            ("tipo", "TEXT DEFAULT 'MANUALE'"),
            ("banca_id", "TEXT"),
            ("numero_ddt", "TEXT"),
            ("data_ddt", "TEXT")
        ]
        
        for colonna, tipo_dato in colonne_da_aggiungere:
            try:
                cur.execute(f"ALTER TABLE fatture ADD COLUMN {colonna} {tipo_dato};")
                db.commit()
            except Exception:
                db.rollback()
            
        # Tabella Righe Fattura
        cur.execute("""
            CREATE TABLE IF NOT EXISTS righe_fattura (
                id SERIAL PRIMARY KEY,
                fattura_id INTEGER NOT NULL,
                prodotto_id INTEGER,
                descrizione TEXT,
                quantita REAL DEFAULT 1.0,
                prezzo_unitario REAL DEFAULT 0.0,
                unita_misura TEXT DEFAULT 'mq',
                FOREIGN KEY (fattura_id) REFERENCES fatture(id) ON DELETE CASCADE
            )
        """)
        
        # Tabella Prodotti
        cur.execute("""
            CREATE TABLE IF NOT EXISTS prodotti (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                prezzo_base REAL DEFAULT 0.0,
                unita_misura TEXT DEFAULT 'mq'
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
    
    elenco_banche = [
        {"id": "BPER", "nome": "BPER Banca di Luserna San Giovanni - IT35S0538730600000004332185"},
        {"id": "POSTE", "nome": "Poste Italiane - IT04B0760110200001078221247"}
    ]
    
    if request.method == "POST":
        numero = request.form.get("numero")
        data = request.form.get("data")
        data_scadenza = request.form.get("data_scadenza")
        cliente_id_raw = request.form.get("cliente_id")
        tipo = request.form.get("tipo", "MANUALE")
        regime_iva = request.form.get("regime_iva", "22")
        banca_id = request.form.get("banca_id")
        totale = request.form.get("totale", 0.0)
        note = request.form.get("note", "")  # Vuoto di default, nessuna alterazione automatica
        stato_pagamento = request.form.get("stato_pagamento", "Non pagata")
        stato = request.form.get("stato", "BOZZA")
        
        cliente_id = None
        cliente_nome = "Cliente Generico"
        if cliente_id_raw and cliente_id_raw.strip():
            try:
                cliente_id = int(cliente_id_raw)
                cur.execute("SELECT nome FROM clienti WHERE id = %s", (cliente_id,))
                cliente = cur.fetchone()
                if cliente:
                    cliente_nome = cliente["nome"]
            except ValueError:
                pass

        cur.execute("""
            INSERT INTO fatture (numero, data, data_scadenza, cliente_id, cliente_nome, tipo, regime_iva, banca_id, totale, note, stato_pagamento, stato)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (numero, data, data_scadenza, cliente_id, cliente_nome, tipo, regime_iva, banca_id, totale, note, stato_pagamento, stato))
        
        db.commit()
        cur.close()
        flash("Fattura creata con successo!", "success")
        return redirect(url_for("fatture"))
        
    cur.execute("SELECT id, nome FROM clienti ORDER BY nome ASC")
    clienti = cur.fetchall()
    cur.close()
    
    from datetime import datetime
    data_oggi = datetime.now().strftime("%Y-%m-%d")
    
    return render_template("nuova_fattura.html", clienti=clienti, banche=elenco_banche, data_oggi=data_oggi)


@app.route("/fattura/<int:fattura_id>")
def vedi_fattura(fattura_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute("SELECT * FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    
    if not f:
        cur.close()
        flash("Fattura non trovata.", "danger")
        return redirect(url_for("fatture"))
        
    cliente = None
    if f["cliente_id"]:
        cur.execute("SELECT * FROM clienti WHERE id = %s", (f["cliente_id"],))
        cliente = cur.fetchone()
    
    cur.execute("SELECT * FROM righe_fattura WHERE fattura_id = %s ORDER BY id ASC", (fattura_id,))
    righe_raw = cur.fetchall()
    cur.close()
    
    # Risolviamo l'errore del template iniettando 'prezzo' come alias di 'prezzo_unitario'
    righe = []
    for r in righe_raw:
        d = dict(r)
        d["prezzo"] = d.get("prezzo_unitario", 0.0)
        righe.append(d)
    
    fattura_dict = dict(f)
    if "regime_iva" not in fattura_dict or not fattura_dict["regime_iva"]: 
        fattura_dict["regime_iva"] = "22"
    if "banca_id" not in fattura_dict: 
        fattura_dict["banca_id"] = ""
    
    valore_totale = fattura_dict.get("totale", 0.0) or 0.0
    
    try:
        aliquota = float(fattura_dict["regime_iva"])
    except:
        aliquota = 22.0
        
    valore_imponibile = valore_totale / (1 + (aliquota / 100.0))
    valore_iva = valore_totale - valore_imponibile
    
    if "totale_pagato" not in fattura_dict:
        fattura_dict["totale_pagato"] = valore_totale if fattura_dict.get("stato_pagamento") == "Pagata" else 0.0

    return render_template(
        "fattura_dettaglio.html", 
        fattura=fattura_dict, 
        cliente=cliente, 
        righe=righe, 
        totale=valore_totale,
        imponibile=valore_imponibile,
        iva=valore_iva
    )


# --- ROTTE DI AGGIORNAMENTO STRUTTURATO ---

@app.route("/aggiorna_testata/<int:fattura_id>", methods=["POST"])
@app.route("/aggiorna_fattura_ajax/<int:fattura_id>", methods=["POST"])
def aggiorna_fattura_ajax(fattura_id):
    db = get_db()
    cur = db.cursor()
    
    if request.is_json:
        data = request.get_json()
        numero = data.get("numero")
        data_doc = data.get("data")
        data_scadenza = data.get("data_scadenza")
        stato_pagamento = data.get("stato_pagamento")
        stato = data.get("stato")
        banca_id = data.get("banca_id")  
        regime_iva = data.get("regime_iva")
        note = data.get("note")
        
        cur.execute("""
            UPDATE fatture 
            SET numero=COALESCE(%s, numero), data=COALESCE(%s, data), data_scadenza=COALESCE(%s, data_scadenza),
                stato_pagamento=COALESCE(%s, stato_pagamento), stato=COALESCE(%s, stato),
                banca_id=COALESCE(%s, banca_id), regime_iva=COALESCE(%s, regime_iva), note=COALESCE(%s, note)
            WHERE id=%s
        """, (numero, data_doc, data_scadenza, stato_pagamento, stato, banca_id, regime_iva, note, fattura_id))
    else:
        numero = request.form.get("numero")
        data_doc = request.form.get("data")
        data_scadenza = request.form.get("data_scadenza")
        stato_pagamento = request.form.get("stato_pagamento")
        stato = request.form.get("stato")
        banca_id = request.form.get("banca_id") 
        regime_iva = request.form.get("regime_iva")
        note = request.form.get("note")
        
        cur.execute("""
            UPDATE fatture 
            SET numero=COALESCE(%s, numero), data=COALESCE(%s, data), data_scadenza=COALESCE(%s, data_scadenza), 
                stato_pagamento=COALESCE(%s, stato_pagamento), stato=COALESCE(%s, stato), 
                banca_id=COALESCE(%s, banca_id), regime_iva=COALESCE(%s, regime_iva), note=COALESCE(%s, note)
            WHERE id=%s
        """, (numero, data_doc, data_scadenza, stato_pagamento, stato, banca_id, regime_iva, note, fattura_id))
        
    db.commit()
    cur.close()
    
    if request.is_json:
        return jsonify({"success": True})
    flash("Fattura salvata con successo.", "success")
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))


@app.route("/add_riga", methods=["POST"])
def add_riga():
    fattura_id = request.form.get("fattura_id")
    descrizione = request.form.get("descrizione")
    quantita = request.form.get("quantita", 1.0)
    prezzo_unitario = request.form.get("prezzo", request.form.get("prezzo_unitario", 0.0))
    unita_misura = request.form.get("unita_misura", "mq")
    
    try:
        quantita = float(quantita)
        prezzo_unitario = float(prezzo_unitario)
    except:
        quantita = 1.0
        prezzo_unitario = 0.0
        
    db = get_db()
    cur = db.cursor()
    
    cur.execute("""
        INSERT INTO righe_fattura (fattura_id, descrizione, quantita, prezzo_unitario, unita_misura)
        VALUES (%s, %s, %s, %s, %s)
    """, (fattura_id, descrizione, quantita, prezzo_unitario, unita_misura))
    
    cur.execute("SELECT SUM(quantita * prezzo_unitario) FROM righe_fattura WHERE fattura_id = %s", (fattura_id,))
    imponibile_totale = cur.fetchone()[0] or 0.0
    
    cur.execute("SELECT regime_iva FROM fatture WHERE id = %s", (fattura_id,))
    regime_iva_raw = cur.fetchone()[0] or "22"
    try:
        aliquota = float(regime_iva_raw)
    except:
        aliquota = 22.0
        
    totale_ivato = imponibile_totale * (1 + (aliquota / 100.0))
    cur.execute("UPDATE fatture SET totale = %s WHERE id = %s", (totale_ivato, fattura_id))
    
    db.commit()
    cur.close()
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))


@app.route("/add_ddt", methods=["POST"])
def add_ddt():
    fattura_id = request.form.get("fattura_id")
    numero_ddt = request.form.get("numero_ddt")
    data_ddt = request.form.get("data_ddt")
    
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE fatture SET numero_ddt = %s, data_ddt = %s WHERE id = %s", (numero_ddt, data_ddt, fattura_id))
    db.commit()
    cur.close()
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))


@app.route("/delete_fattura/<int:fattura_id>")
def delete_fattura(fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM fatture WHERE id = %s", (fattura_id,))
    db.commit()
    cur.close()
    flash("Fattura eliminata con successo.", "success")
    return redirect(url_for("fatture"))

# --- SEZIONE CLIENTI ---
@app.route("/clienti", methods=["GET", "POST"])
def clienti():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if request.method == "POST":
        nome = request.form.get("nome")
        indirizzo = request.form.get("indirizzo")
        partita_iva = request.form.get("partita_iva")
        codice_fiscale = request.form.get("codice_fiscale")
        codice_sdi = request.form.get("codice_sdi")
        pec = request.form.get("pec")
        cur.execute("""
            INSERT INTO clienti (nome, indirizzo, partita_iva, codice_fiscale, codice_sdi, pec)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (nome, indirizzo, partita_iva, codice_fiscale, codice_sdi, pec))
        db.commit()
        flash("Cliente aggiunto con successo!", "success")
        return redirect(url_for("clienti"))
    cur.execute("SELECT * FROM clienti ORDER BY nome ASC")
    elenco_clienti = cur.fetchall()
    cur.close()
    return render_template("clienti.html", clienti=elenco_clienti)

# --- SEZIONE PRODOTTI ---
@app.route("/prodotti", methods=["GET", "POST"])
def prodotti():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if request.method == "POST":
        nome = request.form.get("nome")
        prezzo_base = request.form.get("prezzo_base", 0.0)
        unita_misura = request.form.get("unita_misura", "mq")
        try: prezzo_base = float(prezzo_base)
        except: prezzo_base = 0.0
        cur.execute("INSERT INTO prodotti (nome, prezzo_base, unita_misura) VALUES (%s, %s, %s)", (nome, prezzo_base, unita_misura))
        db.commit()
        return redirect(url_for("prodotti"))
    cur.execute("SELECT * FROM prodotti ORDER BY nome ASC")
    elenco_prodotti = cur.fetchall()
    cur.close()
    return render_template("prodotti.html", prodotti=elenco_prodotti)

@app.route("/modifica_prodotto/<int:prodotto_id>", methods=["POST"])
def modifica_prodotto(prodotto_id):
    db = get_db()
    cur = db.cursor()
    data = request.get_json()
    cur.execute("UPDATE prodotti SET nome=%s, prezzo_base=%s, unita_misura=%s WHERE id=%s", (data.get("nome"), data.get("prezzo_base", 0.0), data.get("unita_misura", "mq"), prodotto_id))
    db.commit()
    cur.close()
    return jsonify({"success": True})

@app.route("/delete_prodotto/<int:prodotto_id>")
def delete_prodotto(prodotto_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM prodotti WHERE id = %s", (prodotto_id,))
    db.commit()
    cur.close()
    return redirect(url_for("prodotti"))

# --- DASHBOARD ANALISI ---
@app.route("/dashboard")
def dashboard():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT COALESCE(SUM(totale),0) as totale_fatturato, COALESCE(SUM(CASE WHEN stato_pagamento='Pagata' THEN totale ELSE 0 END),0) as totale_incassato, COALESCE(SUM(CASE WHEN stato_pagamento='In attesa' THEN totale ELSE 0 END),0) as totale_attesa, COALESCE(SUM(CASE WHEN stato_pagamento='Non pagata' THEN totale ELSE 0 END),0) as totale_non_pagato FROM fatture WHERE stato='CHIUSA'")
    stats = cur.fetchone()
    cur.execute("SELECT COUNT(*) as totale_invii, COUNT(CASE WHEN stato='BOZZA' THEN 1 END) as bozze, COUNT(CASE WHEN stato='CHIUSA' THEN 1 END) as chiuse FROM fatture")
    conteggi = cur.fetchone()
    cur.execute("SELECT SUBSTRING(data FROM 1 FOR 7) as mese, SUM(totale) as totale FROM fatture WHERE stato='CHIUSA' AND data IS NOT NULL AND data!='' GROUP BY SUBSTRING(data FROM 1 FOR 7) ORDER BY mese DESC LIMIT 6")
    trend_mensile = cur.fetchall()[::-1]
    cur.execute("SELECT cliente_nome, SUM(totale) as totale FROM fatture WHERE stato='CHIUSA' GROUP BY cliente_id, cliente_nome ORDER BY totale DESC LIMIT 5")
    top_clienti = cur.fetchall()
    cur.close()
    return render_template("dashboard.html", stats=stats, conteggi=conteggi, trend_mensile=trend_mensile, top_clienti=top_clienti)

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
    return redirect(url_for("fatture"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)