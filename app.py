import os
from datetime import datetime
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, g, jsonify, flash
from config import BANCHE, PASSWORD_ACCESSO
from flask import Response
import json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chiave-segreta-temporanea")
DATABASE_URL = os.environ.get("DATABASE_URL")

# ==============================================================================
# 1. GESTIONE DATABASE & INIZIALIZZAZIONE
# ==============================================================================

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

        # Tabella DDT legati alla fattura
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ddt (
                id SERIAL PRIMARY KEY,
                fattura_id INTEGER NOT NULL,
                numero TEXT NOT NULL,
                data TEXT,
                FOREIGN KEY (fattura_id) REFERENCES fatture(id) ON DELETE CASCADE
            )
        """)
        db.commit()

        # Tabella Righe all'interno dei DDT
        cur.execute("""
            CREATE TABLE IF NOT EXISTS righe_ddt (
                id SERIAL PRIMARY KEY,
                ddt_id INTEGER NOT NULL,
                prodotto_id INTEGER,
                descrizione TEXT,
                quantita REAL DEFAULT 1.0,
                prezzo REAL DEFAULT 0.0,
                unita_misura TEXT DEFAULT 'mq',
                FOREIGN KEY (ddt_id) REFERENCES ddt(id) ON DELETE CASCADE
            )
        """)
        db.commit()

        # Migrazioni per colonne addizionali su Tabella Fatture
        colonne_da_aggiungere = [
            ("regime_iva", "TEXT DEFAULT '22'"),
            ("tipo", "TEXT DEFAULT 'MANUALE'"),
            ("banca_id", "TEXT"),
            ("totale_pagato", "REAL DEFAULT 0.0"),
            ("data_pagamento", "TEXT")
        ]
        
        for colonna, tipo_dato in colonne_da_aggiungere:
            try:
                cur.execute(f"ALTER TABLE fatture ADD COLUMN {colonna} {tipo_dato};")
                db.commit()
            except Exception:
                db.rollback()
            
        # Tabella Righe Fattura (Solo per fatture MANUALE)
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
        
        # Tabella Azienda (per i dati nel PDF)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS azienda (
                id SERIAL PRIMARY KEY,
                nome TEXT,
                indirizzo TEXT,
                partita_iva TEXT,
                codice_fiscale TEXT,
                telefono TEXT,
                email TEXT,
                iban TEXT
            )
        """)
        
        db.commit()
        cur.close()

if DATABASE_URL:
    init_db()


# ==============================================================================
# 2. UTILS & HELPERS
# ==============================================================================

def calcola_totale_fattura(fattura_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # 1. Recupera il regime IVA della fattura
    cur.execute("SELECT regime_iva FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    regime_iva = str(f["regime_iva"]).strip() if f and f["regime_iva"] else "22"

    # 2. Somma i totali delle righe
    cur.execute("SELECT COALESCE(SUM(totale), 0.0) AS imponibile FROM righe_fatture WHERE fattura_id = %s", (fattura_id,))
    res_righe = cur.fetchone()
    imponibile_righe = float(res_righe["imponibile"] or 0.0)

    cur.execute("SELECT COALESCE(SUM(totale), 0.0) AS imponibile FROM righe_ddt WHERE ddt_id IN (SELECT id FROM ddt WHERE fattura_id = %s)", (fattura_id,))
    res_ddt = cur.fetchone()
    imponibile_ddt = float(res_ddt["imponibile"] or 0.0)

    imponibile_totale = imponibile_righe + imponibile_ddt

    # 3. Calcolo dell'IVA e del Totale effettivo
    if regime_iva in ["22", "22.0"]:
        iva = imponibile_totale * 0.22
        totale_finale = imponibile_totale + iva
    else:
        # Se Reverse Charge (RC%), Esente, ecc.
        iva = 0.0
        totale_finale = imponibile_totale

    # 4. Aggiorna la colonna 'totale' nel Database con il valore corretto
    cur.execute("UPDATE fatture SET totale = %s WHERE id = %s", (totale_finale, fattura_id))
    db.commit()
    cur.close()

    return imponibile_totale, iva, totale_finale

# ==============================================================================
# 3. ROTTE FATTURE (VISTA, CREAZIONE, DETTAGLIO, MODIFICA)
# ==============================================================================

@app.route("/")
def index():
    return redirect(url_for("fatture"))


@app.route("/fatture")
def fatture():
    # 1. Recupera i filtri inviati dal modulo di ricerca (GET)
    filtro_cliente = request.args.get("cliente", "").strip() or None
    filtro_stato = request.args.get("stato", "").strip() or None
    filtro_tipo = request.args.get("tipo", "").strip() or None
    
    # Se l'utente clicca su "Reset"
    if request.args.get("azzera"):
        filtro_cliente = filtro_stato = filtro_tipo = None

    db = get_db()
    import psycopg2.extras
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # 2. Query di base con LEFT JOIN per mostrare i nomi reali dei clienti
    query = """
        SELECT f.*, 
               COALESCE(c.nome, f.cliente_nome, 'Cliente Generico') as nome_visualizzato
        FROM fatture f
        LEFT JOIN clienti c ON f.cliente_id = c.id
        WHERE 1=1
    """
    params = []
    
    # 3. Applica i filtri alla query SQL se presenti
    if filtro_cliente:
        query += " AND (c.nome ILIKE %s OR f.cliente_nome ILIKE %s)"
        params.append(f"%{filtro_cliente}%")
        params.append(f"%{filtro_cliente}%")
    if filtro_stato:
        query += " AND f.stato_pagamento = %s"
        params.append(filtro_stato)
    if filtro_tipo:
        query += " AND f.tipo = %s"
        params.append(filtro_tipo)
        
    query += " ORDER BY f.data DESC, f.numero DESC"
    
    cur.execute(query, params)
    elenco_fatture = cur.fetchall()
    cur.close()
    
    return render_template(
        "fatture.html", 
        fatture=elenco_fatture, 
        password_eliminazione=PASSWORD_ACCESSO,
        # Rimanda i valori correnti al template per mantenere i campi compilati
        filtro_cliente=filtro_cliente or "",
        filtro_stato=filtro_stato or "",
        filtro_tipo=filtro_tipo or ""
    )


@app.route("/nuova_fattura", methods=["GET", "POST"])
def nueva_fattura():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    elenco_banche = list(BANCHE.values())
    
    if request.method == "POST":
        cliente_id_raw = request.form.get("cliente_id")
        
        # Regola 1: Obbligo assoluto di scegliere un cliente valido
        if not cliente_id_raw or not cliente_id_raw.strip():
            cur.execute("SELECT id, nome FROM clienti ORDER BY nome ASC")
            clienti = cur.fetchall()
            cur.close()
            flash("Errore: È obbligatorio selezionare un cliente per emettere una fattura.", "danger")
            return render_template("nuova_fattura.html", clienti=clienti, banche=elenco_banche, data_oggi=datetime.now().strftime("%Y-%m-%d"))

        try:
            cliente_id = int(cliente_id_raw)
            cur.execute("SELECT nome FROM clienti WHERE id = %s", (cliente_id,))
            cliente = cur.fetchone()
            if not cliente:
                raise ValueError
            cliente_nome = cliente["nome"]
        except ValueError:
            cur.execute("SELECT id, nome FROM clienti ORDER BY nome ASC")
            clienti = cur.fetchall()
            cur.close()
            flash("Errore: Cliente selezionato non valido.", "danger")
            return render_template("nuova_fattura.html", clienti=clienti, banche=elenco_banche, data_oggi=datetime.now().strftime("%Y-%m-%d"))

        numero = request.form.get("numero")
        data = request.form.get("data")
        data_scadenza = request.form.get("data_scadenza")
        tipo = request.form.get("tipo", "MANUALE")
        banca_id = request.form.get("banca_id")
        totale = request.form.get("totale", 0.0)
        note = request.form.get("note", "")
        stato_pagamento = request.form.get("stato_pagamento", "Non pagata")
        stato = request.form.get("stato", "BOZZA")
        
        # Regola 2: Se fattura FORNITURA, l'IVA è bloccata al 22%
        if tipo == "FORNITURA":
            regime_iva = "22"
        else:
            regime_iva = request.form.get("regime_iva", "22")

        try:
            cur.execute("""
                INSERT INTO fatture (numero, data, data_scadenza, cliente_id, cliente_nome, tipo, regime_iva, banca_id, totale, note, stato_pagamento, stato, totale_pagato)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0.0)
            """, (numero, data, data_scadenza, cliente_id, cliente_nome, tipo, regime_iva, banca_id, totale, note, stato_pagamento, stato))
            db.commit()
            flash("Fattura creata con successo!", "success")
            return redirect(url_for("fatture"))
        except Exception as e:
            db.rollback()
            flash(f"Errore durante l'inserimento: {str(e)}", "danger")
    
    cur.execute("SELECT id, nome FROM clienti ORDER BY nome ASC")
    clienti = cur.fetchall()
    cur.close()
    
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
    
    cur.execute("SELECT * FROM prodotti ORDER BY id ASC")
    prodotti = cur.fetchall()
    
    ddt_list = []
    righe_ddt = []
    righe = []
    
    if f["tipo"] == "FORNITURA":
        # CORREZIONE: Ordina i DDT dal primo inserito (in alto) all'ultimo (in basso)
        cur.execute("SELECT * FROM ddt WHERE fattura_id = %s ORDER BY id ASC", (fattura_id,))
        ddt_list = cur.fetchall()
        
        if ddt_list:
            ddt_ids = [d["id"] for d in ddt_list]
            cur.execute("""
                SELECT id, ddt_id, prodotto_id, descrizione, quantita, prezzo, unita_misura,
                       (quantita * prezzo) as totale
                FROM righe_ddt WHERE ddt_id IN %s ORDER BY id ASC
            """, (tuple(ddt_ids),))
            righe_ddt = cur.fetchall()
    else:
        cur.execute("SELECT * FROM righe_fattura WHERE fattura_id = %s ORDER BY id ASC", (fattura_id,))
        righe_raw = cur.fetchall()
        for r in righe_raw:
            d = dict(r)
            d["prezzo"] = d.get("prezzo_unitario", 0.0)
            d["totale"] = d["quantita"] * d["prezzo"]
            righe.append(d)
            
    cur.close()
    
    fattura_dict = dict(f)
    if "regime_iva" not in fattura_dict or not fattura_dict["regime_iva"]: 
        fattura_dict["regime_iva"] = "22"
    
    valore_totale = fattura_dict.get("totale", 0.0) or 0.0
    try:
        aliquota = float(fattura_dict["regime_iva"])
    except:
        aliquota = 22.0
        
    valore_imponibile = valore_totale / (1 + (aliquota / 100.0))
    valore_iva = valore_totale - valore_imponibile
    
    if "totale_pagato" not in fattura_dict or fattura_dict["totale_pagato"] is None:
        fattura_dict["totale_pagato"] = valore_totale if fattura_dict.get("stato_pagamento") == "Pagata" else 0.0

    return render_template(
        "fattura_dettaglio.html", 
        fattura=fattura_dict, 
        cliente=cliente, 
        prodotti=prodotti,
        ddt_list=ddt_list,
        righe_ddt=righe_ddt,
        righe=righe, 
        totale=valore_totale,
        imponibile=valore_imponibile,
        iva=valore_iva
    )


@app.route("/aggiorna_testata/<int:fattura_id>", methods=["POST"])
@app.route("/aggiorna_fattura_ajax/<int:fattura_id>", methods=["POST"])
def aggiorna_fattura_ajax(fattura_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Controlliamo lo stato attuale della fattura
    cur.execute("SELECT stato, tipo FROM fatture WHERE id = %s", (fattura_id,))
    fattura_attuale = cur.fetchone()
    if not fattura_attuale:
        cur.close()
        if request.is_json:
            return jsonify({"success": False, "error": "Fattura non trovata"}), 404
        flash("Fattura non trovata.", "danger")
        return redirect(url_for("fatture"))

    is_json = request.is_json
    data = request.get_json() if is_json else request.form
    
    # Regola 3: Se CHIUSA, si possono variare SOLO le descrizioni (o note) previo inserimento password
    if fattura_attuale["stato"] == "CHIUSA":
        password = data.get("password")
        if not password or password != PASSWORD_ACCESSO:
            cur.close()
            if is_json:
                return jsonify({"success": False, "error": "La fattura è CHIUSA. Password di sblocco errata o mancante."}), 403
            flash("Impossibile modificare una fattura CHIUSA senza la password corretta.", "danger")
            return redirect(url_for("vedi_fattura", fattura_id=fattura_id))
        
        # Se la password è corretta ed è CHIUSA, aggiorniamo unicamente le note (campo di testo descrittivo)
        note = data.get("note")
        cur.execute("UPDATE fatture SET note = %s WHERE id = %s", (note, fattura_id))
        db.commit()
        cur.close()
        if is_json:
            return jsonify({"success": True, "message": "Note aggiornate correttamente (Fattura Chiusa)"})
        flash("Note della fattura chiusa aggiornate con successo.", "success")
        return redirect(url_for("vedi_fattura", fattura_id=fattura_id))

    # Logica standard se la fattura è APERTA/BOZZA
    numero = data.get("numero")
    data_doc = data.get("data")
    data_scadenza = data.get("data_scadenza")
    data_pagamento = data.get("data_pagamento")
    stato_pagamento = data.get("stato_pagamento")
    stato = data.get("stato")
    banca_id = data.get("banca_id")  
    note = data.get("note")
    totale_pagato = data.get("totale_pagato")
    
    # Regola 2 bis: Se il tipo è (o diventa) FORNITURA, sovrascriviamo l'IVA al 22%
    if fattura_attuale["tipo"] == "FORNITURA" or data.get("tipo") == "FORNITURA":
        regime_iva = "22"
    else:
        regime_iva = data.get("regime_iva")

    if stato_pagamento == "Pagata":
        cur.execute("SELECT totale FROM fatture WHERE id = %s", (fattura_id,))
        f_tot = cur.fetchone()
        if f_tot:
            totale_pagato = f_tot[0]
    elif not is_json and totale_pagato is not None:
        try:
            totale_pagato = float(totale_pagato)
        except:
            totale_pagato = 0.0

    cur.execute("""
        UPDATE fatture 
        SET numero=COALESCE(%s, numero), data=COALESCE(%s, data), data_scadenza=COALESCE(%s, data_scadenza),
            data_pagamento=COALESCE(%s, data_pagamento), stato_pagamento=COALESCE(%s, stato_pagamento), 
            stato=COALESCE(%s, stato), banca_id=COALESCE(%s, banca_id), regime_iva=COALESCE(%s, regime_iva), 
            note=%s, totale_pagato=COALESCE(%s, totale_pagato)
        WHERE id=%s
    """, (numero, data_doc, data_scadenza, data_pagamento, stato_pagamento, stato, banca_id, regime_iva, note, totale_pagato, fattura_id))
        
    db.commit()
    ricalcola_totale_fattura(cur, fattura_id)
    db.commit()
    cur.close()
    
    if is_json:
        return jsonify({"success": True})
    flash("Fattura salvata con successo.", "success")
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))


@app.route("/chiudi_fattura/<int:fattura_id>")
def chiudi_fattura(fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("UPDATE fatture SET stato='CHIUSA' WHERE id=%s", (fattura_id,))
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


# ==============================================================================
# 4. GESTIONE RIGHE MANUALI (TIPO MANUALE)
# ==============================================================================

@app.route("/add_riga", methods=["POST"])
def add_riga():
    fattura_id = request.form.get("fattura_id")
    password = request.form.get("password")
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # Blocco sicurezza
    cur.execute("SELECT stato FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    if f and f["stato"] == "CHIUSA":
        if not password or password != PASSWORD_ACCESSO:
            cur.close()
            flash("Impossibile aggiungere righe a una fattura CHIUSA senza la password corretta.", "danger")
            return redirect(url_for("vedi_fattura", fattura_id=fattura_id))
        
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
    
    cur.execute("""
        INSERT INTO righe_fattura (fattura_id, descrizione, quantita, prezzo_unitario, unita_misura)
        VALUES (%s, %s, %s, %s, %s)
    """, (fattura_id, descrizione, quantita, prezzo_unitario, unita_misura))
    
    ricalcola_totale_fattura(cur, fattura_id)
    db.commit()
    cur.close()
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))


@app.route("/aggiorna_riga_fattura/<int:riga_id>", methods=["POST"])
def aggiorna_riga_fattura(riga_id):
    """Permette di modificare le righe di una fattura MANUALE (compresa descrizione, quantità, prezzo)."""
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        cur.execute("SELECT fattura_id FROM righe_fattura WHERE id = %s", (riga_id,))
        riga_item = cur.fetchone()
        if not riga_item:
            return jsonify({"success": False, "error": "Riga fattura non trovata"}), 404
            
        cur.execute("SELECT stato FROM fatture WHERE id = %s", (riga_item["fattura_id"],))
        f = cur.fetchone()

        data = request.get_json() or {}
        password = data.get("password")

        # Blocco sicurezza
        if f and f["stato"] == "CHIUSA":
            if not password or password != PASSWORD_ACCESSO:
                return jsonify({"success": False, "error": "La fattura è CHIUSA. Password errata o mancante."}), 403

        try:
            quantita = float(data.get("quantita", 1.0))
            prezzo_unitario = float(data.get("prezzo_unitario", 0.0))
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Formato quantità o prezzo non valido"}), 400
        
        descrizione = data.get("descrizione")
        unita_misura = data.get("unita_misura", "mq")

        cur.execute("""
            UPDATE righe_fattura 
            SET quantita = %s, prezzo_unitario = %s, descrizione = COALESCE(%s, descrizione), unita_misura = COALESCE(%s, unita_misura) 
            WHERE id = %s
        """, (quantita, prezzo_unitario, descrizione, unita_misura, riga_id))
        
        ricalcola_totale_fattura(cur, riga_item["fattura_id"])
        db.commit()
        return jsonify({"success": True})
        
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()


@app.route("/delete_riga_fattura/<int:riga_id>/<int:fattura_id>", methods=["POST", "GET"])
def delete_riga_fattura(riga_id, fattura_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    password = request.form.get("password") or request.args.get("password")
    
    # Blocco sicurezza
    cur.execute("SELECT stato FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    if f and f["stato"] == "CHIUSA":
        if not password or password != PASSWORD_ACCESSO:
            cur.close()
            flash("Impossibile eliminare righe da una fattura CHIUSA senza la password corretta.", "danger")
            return redirect(url_for("vedi_fattura", fattura_id=fattura_id))
        
    cur.execute("DELETE FROM righe_fattura WHERE id = %s AND fattura_id = %s", (riga_id, fattura_id))
    ricalcola_totale_fattura(cur, fattura_id)
    db.commit()
    cur.close()
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))

@app.route("/riapri_fattura/<int:id>", methods=["POST"])
def riapri_fattura(id):
    data = request.get_json() or {}
    password_inserita = data.get("password")

    # Verifica la password usando la costante globale del tuo file app.py
    if password_inserita != PASSWORD_ACCESSO:
        return jsonify({"success": False, "error": "Password errata"}), 403

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        # Riporta lo stato a BOZZA
        cur.execute(
            "UPDATE fatture SET stato = 'BOZZA' WHERE id = %s", 
            (id,)
        )
        db.commit()
        return jsonify({"success": True, "message": "Fattura sbloccata con successo!"})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()

# ==============================================================================
# 5. GESTIONE DDT & RIGHE DDT (TIPO FORNITURA)
# ==============================================================================

@app.route("/add_ddt", methods=["POST"])
def add_ddt():
    fattura_id = request.form.get("fattura_id")
    numero = request.form.get("numero")
    data = request.form.get("data")  
    password = request.form.get("password")
    
    if not fattura_id:
        flash("ID Fattura mancante.", "danger")
        return redirect(url_for("index"))

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        # Blocco sicurezza
        cur.execute("SELECT stato FROM fatture WHERE id = %s", (fattura_id,))
        f = cur.fetchone()
        if not f:
            flash("Fattura non trovata.", "danger")
            return redirect(url_for("index"))
            
        if f["stato"] == "CHIUSA":
            if not password or password != PASSWORD_ACCESSO:
                flash("Impossibile aggiungere DDT a una fattura CHIUSA senza la password corretta.", "danger")
                return redirect(url_for("vedi_fattura", fattura_id=fattura_id))
            
        cur.execute(
            "INSERT INTO ddt (fattura_id, numero, data) VALUES (%s, %s, %s)", 
            (fattura_id, numero, data)
        )
        db.commit()
    except Exception as e:
        db.rollback()
        flash(f"Errore durante il salvataggio: {str(e)}", "danger")
    finally:
        cur.close()
        
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))


@app.route("/aggiorna_ddt/<int:ddt_id>", methods=["POST"])
def aggiorna_ddt(ddt_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        # Blocco sicurezza
        cur.execute("SELECT fattura_id FROM ddt WHERE id = %s", (ddt_id,))
        ddt_item = cur.fetchone()
        if not ddt_item:
            return jsonify({"success": False, "error": "DDT non trovato"}), 404
            
        cur.execute("SELECT stato FROM fatture WHERE id = %s", (ddt_item["fattura_id"],))
        f = cur.fetchone()
        
        data = request.get_json() or {}
        password = data.get("password")

        if f and f["stato"] == "CHIUSA":
            if not password or password != PASSWORD_ACCESSO:
                return jsonify({"success": False, "error": "Impossibile modificare DDT di una fattura CHIUSA senza password."}), 403
                
        numero = data.get("numero")
        data_ddt = data.get("data")
        
        cur.execute(
            "UPDATE ddt SET numero = %s, data = %s WHERE id = %s", 
            (numero, data_ddt, ddt_id)
        )
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()


@app.route("/delete_ddt/<int:ddt_id>/<int:fattura_id>", methods=["POST", "GET"])
def delete_ddt(ddt_id, fattura_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    password = request.form.get("password") or request.args.get("password")

    try:
        # Blocco sicurezza
        cur.execute("SELECT stato FROM fatture WHERE id = %s", (fattura_id,))
        f = cur.fetchone()
        if not f:
            flash("Fattura non trovata.", "danger")
            return redirect(url_for("index"))
            
        if f["stato"] == "CHIUSA":
            if not password or password != PASSWORD_ACCESSO:
                flash("Impossibile eliminare DDT da una fattura CHIUSA senza la password corretta.", "danger")
                return redirect(url_for("vedi_fattura", fattura_id=fattura_id))
            
        cur.execute("DELETE FROM ddt WHERE id = %s AND fattura_id = %s", (ddt_id, fattura_id))
        ricalcola_totale_fattura(cur, fattura_id)
        db.commit()
    except Exception as e:
        db.rollback()
        flash(f"Errore durante l'eliminazione: {str(e)}", "danger")
    finally:
        cur.close()
        
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))

@app.route("/add_riga_prodotto", methods=["POST"])
def add_riga_prodotto():
    fattura_id = request.form.get("fattura_id")
    ddt_id = request.form.get("ddt_id")
    prodotto_id = request.form.get("prodotto_id")
    quantita_raw = request.form.get("quantita", 1.0)
    prezzo_override = request.form.get("prezzo_override")
    password = request.form.get("password")

    if not fattura_id or not ddt_id or not prodotto_id:
        return jsonify({"success": False, "error": "Dati obbligatori mancanti"}), 400

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        # Blocco sicurezza
        cur.execute("SELECT stato FROM fatture WHERE id = %s", (fattura_id,))
        f = cur.fetchone()
        if not f:
            return jsonify({"success": False, "error": "Fattura non trovata"}), 404
        if f["stato"] == "CHIUSA":
            if not password or password != PASSWORD_ACCESSO:
                return jsonify({"success": False, "error": "Impossibile modificare una fattura CHIUSA senza password."}), 403
            
        try:
            quantita = float(quantita_raw)
        except (ValueError, TypeError):
            quantita = 1.0

        cur.execute("SELECT nome, prezzo_base, unita_misura FROM prodotti WHERE id = %s", (prodotto_id,))
        p = cur.fetchone()
        
        if not p:
            return jsonify({"success": False, "error": "Prodotto non trovato"}), 404
            
        descrizione = p["nome"]
        unita_misura = p["unita_misura"]
        
        # Gestione Punto 3: Calcolo dinamico dell'ultimo prezzo applicato al cliente
        if prezzo_override:
            try:
                prezzo = float(prezzo_override)
            except (ValueError, TypeError):
                prezzo = float(p["prezzo_base"])
        else:
            cur.execute("""
                SELECT rd.prezzo FROM righe_ddt rd
                JOIN ddt d ON rd.ddt_id = d.id
                JOIN fatture f ON d.fattura_id = f.id
                WHERE f.cliente_id = (SELECT cliente_id FROM fatture WHERE id = %s)
                  AND rd.prodotto_id = %s
                ORDER BY f.data DESC, d.data DESC, rd.id DESC LIMIT 1
            """, (fattura_id, prodotto_id))
            storico = cur.fetchone()
            
            if storico:
                prezzo = float(storico["prezzo"])
            else:
                prezzo = float(p["prezzo_base"])
            
        cur.execute("""
            INSERT INTO righe_ddt (ddt_id, prodotto_id, descrizione, quantita, prezzo, unita_misura)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (ddt_id, prodotto_id, descrizione, quantita, prezzo, unita_misura))
        
        ricalcola_totale_fattura(cur, fattura_id)
        db.commit()
        return jsonify({"success": True})
        
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()


@app.route("/aggiorna_riga_ddt/<int:riga_id>", methods=["POST"])
def aggiorna_riga_ddt(riga_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    try:
        # Blocco sicurezza
        cur.execute("SELECT ddt_id FROM righe_ddt WHERE id = %s", (riga_id,))
        riga_item = cur.fetchone()
        if not riga_item:
            return jsonify({"success": False, "error": "Riga DDT non trovata"}), 404
            
        cur.execute("SELECT fattura_id FROM ddt WHERE id = %s", (riga_item["ddt_id"],))
        ddt_item = cur.fetchone()
        if not ddt_item:
            return jsonify({"success": False, "error": "DDT associato non trovato"}), 404
            
        cur.execute("SELECT stato FROM fatture WHERE id = %s", (ddt_item["fattura_id"],))
        f = cur.fetchone()

        data = request.get_json() or {}
        password = data.get("password")

        if f and f["stato"] == "CHIUSA":
            if not password or password != PASSWORD_ACCESSO:
                return jsonify({"success": False, "error": "Impossibile modificare prodotti di una fattura CHIUSA senza password."}), 403

        try:
            quantita = float(data.get("quantita", 1.0))
            prezzo = float(data.get("prezzo", 0.0))
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Formato quantità o prezzo non valido"}), 400
        
        descrizione = data.get("descrizione")

        cur.execute("""
            UPDATE righe_ddt 
            SET quantita = %s, prezzo = %s, descrizione = COALESCE(%s, descrizione) 
            WHERE id = %s RETURNING ddt_id
        """, (quantita, prezzo, descrizione, riga_id))
        ddt_id = cur.fetchone()["ddt_id"]
        
        cur.execute("SELECT fattura_id FROM ddt WHERE id = %s", (ddt_id,))
        fattura_id = cur.fetchone()["fattura_id"]
        
        ricalcola_totale_fattura(cur, fattura_id)
        db.commit()
        return jsonify({"success": True})
        
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()


@app.route("/delete_riga_ddt/<int:riga_id>/<int:fattura_id>", methods=["POST", "GET"])
def delete_riga_ddt(riga_id, fattura_id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    password = request.form.get("password") or request.args.get("password")
    
    # Blocco sicurezza
    cur.execute("SELECT stato FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    if f and f["stato"] == "CHIUSA":
        if not password or password != PASSWORD_ACCESSO:
            cur.close()
            return jsonify({"success": False, "error": "Impossibile eliminare prodotti da una fattura CHIUSA senza password."}), 403
        
    cur.execute("DELETE FROM righe_ddt WHERE id = %s", (riga_id,))
    ricalcola_totale_fattura(cur, fattura_id)
    db.commit()
    cur.close()
    
    if request.is_json or request.method == "POST":
        return jsonify({"success": True})
    return redirect(url_for("vedi_fattura", fattura_id=fattura_id))


# ==============================================================================
# 6. ESPORTAZIONE PDF (GENERAZIONE E DOWNLOAD DIRETTO)
# ==============================================================================

@app.route("/pdf/<int:fattura_id>")
def genera_pdf_fattura(fattura_id):
    import psycopg2.extras
    from io import BytesIO
    from xhtml2pdf import pisa

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    try:
        # 1. Recupera la fattura e i dati del cliente
        cur.execute("""
            SELECT f.*, 
                   c.nome AS cliente_nome, 
                   c.indirizzo, 
                   c.partita_iva, 
                   c.codice_fiscale, 
                   c.codice_sdi, 
                   c.pec
            FROM fatture f
            LEFT JOIN clienti c ON f.cliente_id = c.id
            WHERE f.id = %s
        """, (fattura_id,))
        fattura = cur.fetchone()

        if not fattura:
            cur.close()
            return "Fattura non trovata", 404

        fattura_dict = dict(fattura)

        # 2. Recupera le righe della fattura (NOME TABELLA CORRETTO: righe_fattura)
        cur.execute("SELECT * FROM righe_fattura WHERE fattura_id = %s ORDER BY id ASC", (fattura_id,))
        righe_manuali = [dict(r) for r in cur.fetchall()]

        # 3. Recupera le righe dei DDT (se fattura di tipo Fornitura)
        cur.execute("""
            SELECT rd.* 
            FROM righe_ddt rd
            JOIN ddt d ON rd.ddt_id = d.id
            WHERE d.fattura_id = %s
            ORDER BY d.id ASC, rd.id ASC
        """, (fattura_id,))
        righe_ddt = [dict(r) for r in cur.fetchall()]

        tutte_le_righe = righe_manuali + righe_ddt

        # 4. Calcolo imponibile
        imponibile = 0.0
        for r in tutte_le_righe:
            imp = r.get("totale")
            if imp is None:
                q = float(r.get("quantita") or 0.0)
                p = float(r.get("prezzo") or 0.0)
                imp = q * p
            imponibile += float(imp or 0.0)

        # 5. Gestione IVA e Reverse Charge
        regime = str(fattura_dict.get("regime_iva", "") or "22").strip().upper()
        if regime in ["22", "22.0"]:
            iva = imponibile * 0.22
            totale = imponibile + iva
            nota_iva = ""
        else:
            iva = 0.0
            totale = imponibile
            nota_iva = "Operazione in Reverse Charge / Esente IVA"

        cur.close()

        # 6. Renderizza HTML per PDF
        rendered_html = render_template(
            "pdf_fattura.html",
            fattura=fattura_dict,
            righe=tutte_le_righe,
            imponibile=imponibile,
            iva=iva,
            totale=totale,
            nota_iva=nota_iva
        )

        # 7. Generazione PDF
        pdf_buffer = BytesIO()
        pisa_status = pisa.CreatePDF(rendered_html, dest=pdf_buffer)

        if pisa_status.err:
            return f"Errore durante la generazione del PDF: {pisa_status.err}", 500

        pdf_buffer.seek(0)
        num_fattura = fattura_dict.get("numero", "ND")
        filename = f"Fattura_{num_fattura}.pdf"

        return Response(
            pdf_buffer.read(),
            mimetype="application/pdf",
            headers={"Content-Disposition": f"inline; filename={filename}"}
        )

    except Exception as e:
        if 'cur' in locals() and cur:
            cur.close()
        print(f"CRASH GENERAZIONE PDF: {e}")
        return f"Errore interno del server: {str(e)}", 500

# ==============================================================================
# 7. SEZIONE CLIENTI
# ==============================================================================

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


@app.route("/modifica_cliente/<int:cliente_id>", methods=["POST"])
def modifica_cliente(cliente_id):
    db = get_db()
    cur = db.cursor()
    
    if request.is_json:
        data = request.get_json()
        nome = data.get("nome")
        indirizzo = data.get("indirizzo")
        partita_iva = data.get("partita_iva")
        codice_fiscale = data.get("codice_fiscale")
        codice_sdi = data.get("codice_sdi")
        pec = data.get("pec")
    else:
        nome = request.form.get("nome")
        indirizzo = request.form.get("indirizzo")
        partita_iva = request.form.get("partita_iva")
        codice_fiscale = request.form.get("codice_fiscale")
        codice_sdi = request.form.get("codice_sdi")
        pec = request.form.get("pec")

    cur.execute("""
        UPDATE clienti 
        SET nome=%s, indirizzo=%s, partita_iva=%s, codice_fiscale=%s, codice_sdi=%s, pec=%s 
        WHERE id=%s
    """, (nome, indirizzo, partita_iva, codice_fiscale, codice_sdi, pec, cliente_id))
    
    db.commit()
    cur.close()
    return jsonify({"success": True})


@app.route("/delete_cliente/<int:cliente_id>")
def delete_cliente(cliente_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM clienti WHERE id = %s", (cliente_id,))
    db.commit()
    cur.close()
    flash("Cliente eliminato con successo.", "success")
    return redirect(url_for("clienti"))


# ==============================================================================
# 8. SEZIONE PRODOTTI
# ==============================================================================

@app.route("/prodotti", methods=["GET", "POST"])
def prodotti():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if request.method == "POST":
        nome = request.form.get("nome")
        prezzo_base = request.form.get("prezzo_base", 0.0)
        unita_misura = request.form.get("unita_misura", "mq")
        try: 
            prezzo_base = float(prezzo_base)
        except (ValueError, TypeError): 
            prezzo_base = 0.0
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
    data = request.get_json() or {}
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

# ==============================================================================
# 9. DASHBOARD & STATISTICHE
# ==============================================================================

@app.route("/dashboard")
def dashboard():
    import psycopg2.extras

    # 1. Recupera i parametri dei filtri dalla richiesta GET
    filtro_inizio = request.args.get("inizio", "").strip() or None
    filtro_fine = request.args.get("fine", "").strip() or None
    filtro_cliente = request.args.get("cliente", "").strip() or None
    filtro_tipo = request.args.get("tipo", "").strip() or None
    filtro_stato = request.args.get("stato_pagamento", "").strip() or None
    
    if request.args.get("azzera"):
        filtro_inizio = filtro_fine = filtro_cliente = filtro_tipo = filtro_stato = None

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 2. Query per recuperare le fatture filtrate
    query = """
        SELECT f.id,
               f.numero AS numero_fattura, 
               f.data AS data_fattura,
               f.totale AS importo_totale,
               f.note,
               f.stato_pagamento,
               f.data_pagamento,
               f.totale_pagato,
               COALESCE(c.nome, 'Cliente Generico') as cliente_nome,
               COALESCE(c.nome, 'Cliente Generico') as cliente
        FROM fatture f
        LEFT JOIN clienti c ON f.cliente_id = c.id
        WHERE 1=1
    """
    params = []

    if filtro_inizio:
        query += " AND f.data >= %s"
        params.append(filtro_inizio)
    if filtro_fine:
        query += " AND f.data <= %s"
        params.append(filtro_fine)
    if filtro_cliente:
        query += " AND (c.nome ILIKE %s)"
        params.append(f"%{filtro_cliente}%")
    if filtro_tipo:
        query += " AND f.tipo = %s"
        params.append(filtro_tipo)
    if filtro_stato:
        query += " AND f.stato_pagamento = %s"
        params.append(filtro_stato)

    query += " ORDER BY f.data DESC, f.id DESC"
    
    try:
        cur.execute(query, params)
        fatture = cur.fetchall()
    except Exception as e:
        print(f"Errore query fatture: {e}")
        fatture = []

    # 3. Calcolo KPI
    totale_generale = 0.0
    totale_pagato = 0.0

    for f in fatture:
        imp_tot = float(f["importo_totale"] or 0.0)
        totale_generale += imp_tot
        
        stato = (f["stato_pagamento"] or "").lower()
        if stato == "pagato":
            totale_pagato += imp_tot
        elif stato == "parziale":
            totale_pagato += float(f["totale_pagato"] or 0.0)

    totale_mancante = max(0.0, totale_generale - totale_pagato)

    # 4. Elenco clienti per il menu a tendina
    try:
        cur.execute("SELECT nome FROM clienti ORDER BY nome ASC")
        clienti_lista = [r["nome"] for r in cur.fetchall()]
    except Exception:
        clienti_lista = []

    cur.close()

    # 5. Recupera la password di sblocco
    from config import PASSWORD_ACCESSO
    password_sblocco = PASSWORD_ACCESSO

    return render_template(
        "dashboard.html",
        fatture=fatture,
        totale_generale=totale_generale,
        totale_pagato=totale_pagato,
        totale_mancante=totale_mancante,
        filtro_inizio=filtro_inizio or "",
        filtro_fine=filtro_fine or "",
        filtro_cliente=filtro_cliente or "",
        filtro_tipo=filtro_tipo or "",
        filtro_stato=filtro_stato or "",
        clienti_lista=clienti_lista,
        password_sblocco=password_sblocco
    )


# ==============================================================================
# 10. API DI AGGIORNAMENTO STATO IN TEMPO REALE (DASHBOARD)
# ==============================================================================

@app.route("/api/aggiorna_stato", methods=["POST"])
def api_aggiorna_stato():
    data = request.get_json() or {}
    fattura_id = data.get("id")
    stato = data.get("stato")
    data_pagamento = data.get("data_pagamento", "").strip() or None
    importo_pagato = data.get("importo_pagato")

    if not fattura_id:
        return jsonify({"success": False, "message": "ID fattura mancante."}), 400

    if importo_pagato is not None and str(importo_pagato).strip() != "":
        try:
            importo_pagato = float(importo_pagato)
        except (ValueError, TypeError):
            importo_pagato = None
    else:
        importo_pagato = None

    db = get_db()
    cur = db.cursor()

    try:
        try:
            cur.execute("ALTER TABLE fatture ADD COLUMN IF NOT EXISTS totale_pagato NUMERIC(10,2) DEFAULT 0.0;")
            db.commit()
        except Exception:
            db.rollback()

        cur.execute("""
            UPDATE fatture 
            SET stato_pagamento = %s, 
                data_pagamento = %s, 
                totale_pagato = %s
            WHERE id = %s
        """, (stato, data_pagamento, importo_pagato, fattura_id))
        
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        print(f"Errore aggiornamento stato fattura {fattura_id}: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()


# ==============================================================================
# 11. NOTE 
# ==============================================================================

@app.route("/note")
def note_page():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS note (
                id SERIAL PRIMARY KEY,
                titolo TEXT DEFAULT '',
                contenuto TEXT DEFAULT '',
                data_creazione TEXT DEFAULT CURRENT_DATE::TEXT,
                data_modifica TEXT DEFAULT CURRENT_DATE::TEXT
            );
        """)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Errore creazione tabella note: {e}")

    try:
        cur.execute("""
            SELECT id, titolo, contenuto, 
                   COALESCE(data_modifica::TEXT, data_creazione::TEXT) as data_modifica 
            FROM note 
            ORDER BY id DESC
        """)
        elenco_note = cur.fetchall()
    except Exception as e:
        db.rollback()
        print(f"Errore lettura note: {e}")
        elenco_note = []
    finally:
        cur.close()
        
    return render_template("note.html", note=elenco_note)


@app.route("/nota/<int:id>")
def get_nota_api(id):
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM note WHERE id = %s", (id,))
    nota = cur.fetchone()
    cur.close()
    
    if not nota:
        return jsonify({"error": "Nota non trovata"}), 404
        
    return jsonify({
        "id": nota["id"],
        "titolo": nota["titolo"],
        "contenuto": nota["contenuto"]
    })


@app.route("/nuova_nota", methods=["POST"])
def nuova_nota_api():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    data_oggi = datetime.now().strftime("%Y-%m-%d")
    try:
        cur.execute("""
            INSERT INTO note (titolo, contenuto, data_creazione, data_modifica) 
            VALUES ('Senza titolo', '', %s::TEXT, %s::TEXT) 
            RETURNING id
        """, (data_oggi, data_oggi))
        nuovo_id = cur.fetchone()["id"]
        db.commit()
        return jsonify({"success": True, "id": nuovo_id})
    except Exception:
        db.rollback()
        try:
            cur.execute("""
                INSERT INTO note (titolo, contenuto, data_creazione, data_modifica) 
                VALUES ('Senza titolo', '', NOW(), NOW()) 
                RETURNING id
            """)
            nuovo_id = cur.fetchone()["id"]
            db.commit()
            return jsonify({"success": True, "id": nuovo_id})
        except Exception as e_inner:
            db.rollback()
            print(f"Errore creazione nota: {e_inner}")
            return jsonify({"success": False, "error": str(e_inner)}), 500
    finally:
        cur.close()


@app.route("/salva_nota/<int:id>", methods=["POST"])
def salva_nota_api(id):
    data = request.get_json() or {}
    titolo = data.get("titolo", "").strip()
    contenuto = data.get("contenuto", "")
    data_oggi = datetime.now().strftime("%Y-%m-%d")

    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("""
            UPDATE note 
            SET titolo = %s, contenuto = %s, data_modifica = %s::TEXT 
            WHERE id = %s
        """, (titolo if titolo else "Senza titolo", contenuto, data_oggi, id))
        db.commit()
        return jsonify({"success": True})
    except Exception:
        db.rollback()
        try:
            cur.execute("""
                UPDATE note 
                SET titolo = %s, contenuto = %s, data_modifica = NOW() 
                WHERE id = %s
            """, (titolo if titolo else "Senza titolo", contenuto, id))
            db.commit()
            return jsonify({"success": True})
        except Exception as e_inner:
            db.rollback()
            print(f"Errore salvataggio nota: {e_inner}")
            return jsonify({"success": False, "error": str(e_inner)}), 500
    finally:
        cur.close()


@app.route("/elimina_nota/<int:id>", methods=["POST"])
def elimina_nota_api(id):
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM note WHERE id = %s", (id,))
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        cur.close()


# ==============================================================================
# 12. DOWNLOAD DATI CLIENTI, PRODOTTI, FATTURE (BACKUP TXT)
# ==============================================================================

@app.route("/export_fattura_backup")
def export_fattura_backup():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    query = """
        SELECT f.*, c.nome AS cliente_nome 
        FROM fatture f
        LEFT JOIN clienti c ON f.cliente_id = c.id
        ORDER BY f.data DESC, f.id DESC
    """
    cur.execute(query)
    tutte_fatture = cur.fetchall()
    
    if not tutte_fatture:
        cur.close()
        return "Nessuna fattura trovata", 404

    ultima_fattura = tutte_fatture[0]
    num_ultima = ultima_fattura.get('numero', '-')
    cliente_ultimo = ultima_fattura.get('cliente_nome', 'Sconosciuto')

    output = f"Aggiunta Fattura n. {num_ultima}  {cliente_ultimo}\n"
    output += "=========================================================================================\n"
    output += "                           REPORT GENERALE BACKUP FATTURE                                \n"
    output += "=========================================================================================\n\n"
    
    for f in tutte_fatture:
        imp = float(f.get('totale', 0.0) or 0.0)
        output += f"N. FATTURA: {f.get('numero', '-')} | DATA: {f.get('data', '-')}\n"
        output += f"CLIENTE:    {f.get('cliente_nome', 'Sconosciuto')}\n"
        output += f"IMPORTO:    € {imp:.2f}\n"
        output += f"STATO PAG.: {f.get('stato_pagamento', '-')}\n"
        output += f"NOTE/CANT.: {f.get('note', '') or '-'}\n"
        output += "-----------------------------------------------------------------------------------------\n"
        
    cur.close()
    
    timestamp = datetime.now().strftime("%d.%m.%y_%H.%M")
    filename = f"fatture_{timestamp}.txt"
    return Response(output, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename={filename}"})


@app.route("/export_clienti_backup")
def export_clienti_backup():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute("SELECT * FROM clienti ORDER BY nome ASC")
    clienti = cur.fetchall()
    
    cur.execute("SELECT nome FROM clienti ORDER BY id DESC LIMIT 1")
    ultimo_inserito = cur.fetchone()
    cur.close()
    
    nome_ultimo = ultimo_inserito['nome'] if ultimo_inserito else '-'
    
    output = f"Aggiunto Cliente {nome_ultimo}\n"
    output += "========================================\n"
    output += "=== LISTA CLIENTI ===\n"
    output += "========================================\n\n"
    
    for c in clienti:
        output += (
            f"ID: {c['id']}\n"
            f"Nome: {c['nome']}\n"
            f"Partita IVA: {c.get('partita_iva','')}\n"
            f"Codice Fiscale: {c.get('codice_fiscale','')}\n"
            f"Codice SDI: {c.get('codice_sdi','')}\n"
            f"PEC: {c.get('pec','')}\n"
            f"Indirizzo: {c.get('indirizzo','')}\n"
            f"----------------------------------------\n"
        )
    
    timestamp = datetime.now().strftime("%d.%m.%y_%H.%M")
    filename = f"clienti_{timestamp}.txt"
    return Response(output, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename={filename}"})


@app.route("/export_prodotti_backup")
def export_prodotti_backup():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute("SELECT * FROM prodotti ORDER BY nome ASC")
    prodotti = cur.fetchall()
    
    cur.execute("SELECT nome FROM prodotti ORDER BY id DESC LIMIT 1")
    ultimo_inserito = cur.fetchone()
    cur.close()
    
    nome_ultimo = ultimo_inserito['nome'] if ultimo_inserito else '-'
    
    output = f"Aggiunto Prodotto {nome_ultimo}\n"
    output += "========================================\n"
    output += "=== LISTA PRODOTTI ===\n"
    output += "========================================\n\n"
    
    for p in prodotti:
        prezzo_base = float(p.get('prezzo_base', 0.0) or 0.0)
        output += f"ID: {p['id']}\nNome: {p['nome']}\nUnità di Misura: {p['unita_misura']}\nPrezzo Base: €{prezzo_base:.2f}\n----------------------------------------\n"
    
    timestamp = datetime.now().strftime("%d.%m.%y_%H.%M")
    filename = f"prodotti_{timestamp}.txt"
    return Response(output, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename={filename}"})


# ==============================================================================
# 13. AVVIO APPLICAZIONE
# ==============================================================================

if __name__ == "__main__":
    app.run(debug=True)