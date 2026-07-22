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
def ricalcola_totale_fattura(cur, fattura_id):
    # 1. Recupera il regime_iva e il tipo della fattura
    cur.execute("SELECT tipo, regime_iva FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    if not f:
        return

    regime_str = str(f["regime_iva"] or "22").strip().lower()
    
    # Verifica se è Reverse Charge o Esente IVA (Aliquota 0)
    is_rc_or_zero = any(term in regime_str for term in ["0", "reverse", "esente", "non imponibile", "rc"])

    # 2. Somma l'imponibile delle righe
    if f["tipo"] == "FORNITURA":
        cur.execute("""
            SELECT COALESCE(SUM(rd.quantita * rd.prezzo), 0)
            FROM ddt d
            JOIN righe_ddt rd ON d.id = rd.ddt_id
            WHERE d.fattura_id = %s
        """, (fattura_id,))
    else:
        cur.execute("""
            SELECT COALESCE(SUM(quantita * prezzo_unitario), 0)
            FROM righe_fattura
            WHERE fattura_id = %s
        """, (fattura_id,))
        
    imponibile = float(cur.fetchone()[0] or 0.0)

    # 3. Calcola il totale CORRETTO
    if is_rc_or_zero:
        totale_finale = imponibile  # NESSUNA IVA
    else:
        totale_finale = imponibile * 1.22  # IVA 22%

    # 4. Aggiorna il totale reale nel database
    cur.execute("UPDATE fatture SET totale = %s WHERE id = %s", (totale_finale, fattura_id))

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
        
        # CORREZIONE: Permette di impostare l'IVA anche a 0 (es. Reverse Charge)
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
            d["totale"] = float(d["quantita"] or 0.0) * float(d["prezzo"] or 0.0)
            righe.append(d)
            
    cur.close()
    
    fattura_dict = dict(f)
    regime_str = str(fattura_dict.get("regime_iva") or "22").strip().lower()
    
    # Determina l'aliquota per la visualizzazione
    if any(term in regime_str for term in ["0", "reverse", "esente", "non imponibile", "rc"]):
        aliquota = 0.0
    else:
        try:
            import re
            numeri = re.findall(r"\d+\.?\d*", regime_str)
            aliquota = float(numeri[0]) if numeri else 22.0
        except:
            aliquota = 22.0

    # --- CALCOLO CORRETTO DEI TOTALI DAI DATI DELLE RIGHE ---
    if f["tipo"] == "FORNITURA":
        valore_imponibile = sum(float(r["totale"] or 0.0) for r in righe_ddt)
    else:
        valore_imponibile = sum(float(r["totale"] or 0.0) for r in righe)

    if aliquota == 0.0:
        valore_iva = 0.0
        valore_totale = valore_imponibile
    else:
        valore_iva = valore_imponibile * (aliquota / 100.0)
        valore_totale = valore_imponibile + valore_iva

    # Aggiorna il valore per il template
    fattura_dict["totale"] = valore_totale

    if "totale_pagato" not in fattura_dict or fattura_dict["totale_pagato"] is None:
        fattura_dict["totale_pagato"] = valore_totale if str(fattura_dict.get("stato_pagamento")).lower() in ["pagata", "pagato"] else 0.0

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
    
    cur.execute("SELECT stato, tipo, regime_iva FROM fatture WHERE id = %s", (fattura_id,))
    fattura_attuale = cur.fetchone()
    if not fattura_attuale:
        cur.close()
        if request.is_json:
            return jsonify({"success": False, "error": "Fattura non trovata"}), 404
        flash("Fattura non trovata.", "danger")
        return redirect(url_for("fatture"))

    is_json = request.is_json
    data = request.get_json() if is_json else request.form
    
    # Se CHIUSA, gestisci sblocco tramite password
    if fattura_attuale["stato"] == "CHIUSA":
        password = data.get("password")
        if not password or password != PASSWORD_ACCESSO:
            cur.close()
            if is_json:
                return jsonify({"success": False, "error": "La fattura è CHIUSA. Password di sblocco errata o mancante."}), 403
            flash("Impossibile modificare una fattura CHIUSA senza la password corretta.", "danger")
            return redirect(url_for("vedi_fattura", fattura_id=fattura_id))
        
        note = data.get("note")
        cur.execute("UPDATE fatture SET note = %s WHERE id = %s", (note, fattura_id))
        db.commit()
        cur.close()
        if is_json:
            return jsonify({"success": True, "message": "Note aggiornate correttamente (Fattura Chiusa)"})
        flash("Note della fattura chiusa aggiornate con successo.", "success")
        return redirect(url_for("vedi_fattura", fattura_id=fattura_id))

    # Logica per fattura BOZZA/APERTA
    numero = data.get("numero")
    data_doc = data.get("data")
    data_scadenza = data.get("data_scadenza")
    data_pagamento = data.get("data_pagamento")
    stato_pagamento = data.get("stato_pagamento")
    stato = data.get("stato")
    banca_id = data.get("banca_id")  
    note = data.get("note")
    totale_pagato = data.get("totale_pagato")
    totale_manuale = data.get("totale")
    
    # Mantiene il regime IVA selezionato o quello attuale
    regime_iva = data.get("regime_iva")
    if regime_iva is None:
        regime_iva = fattura_attuale["regime_iva"]

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
            note=%s, totale_pagato=COALESCE(%s, totale_pagato),
            totale=COALESCE(%s, totale)
        WHERE id=%s
    """, (numero, data_doc, data_scadenza, data_pagamento, stato_pagamento, stato, banca_id, regime_iva, note, totale_pagato, totale_manuale, fattura_id))
        
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
def genera_pdf(fattura_id):
    import io
    import psycopg2.extras
    from xhtml2pdf import pisa
    from flask import make_response, render_template
    from datetime import datetime

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # 1. Recupera la fattura
    cur.execute("SELECT * FROM fatture WHERE id = %s", (fattura_id,))
    f_raw = cur.fetchone()
    
    if not f_raw:
        cur.close()
        return "Errore: Fattura non trovata.", 404
        
    f = dict(f_raw)
        
    # 2. Recupera i dati del cliente (Cessionario)
    cliente = None
    if f.get("cliente_id"):
        cur.execute("SELECT * FROM clienti WHERE id = %s", (f["cliente_id"],))
        c_raw = cur.fetchone()
        if c_raw:
            cliente = dict(c_raw)
        
    # 3. Recupera i dati dell'azienda (Cedente)
    cur.execute("SELECT * FROM azienda LIMIT 1")
    az_raw = cur.fetchone()
    
    if az_raw:
        azienda = dict(az_raw)
    else:
        azienda = {
            "nome": "La Tua Ditta S.r.l.",
            "indirizzo": "Via Roma 123, Torino (TO)",
            "partita_iva": "IT12345678901",
            "codice_fiscale": "12345678901",
            "telefono": "+39 011 123456",
            "email": "info@lazuaditta.it"
        }
        
    # 4. Associa i dettagli della banca selezionata usando il dizionario BANCHE globale
    banca_selezionata = None
    try:
        if f.get("banca_id") and f["banca_id"] in BANCHE:
            banca_selezionata = BANCHE[f["banca_id"]]
    except NameError:
        try:
            from config import BANCHE
            if f.get("banca_id") and f["banca_id"] in BANCHE:
                banca_selezionata = BANCHE[f["banca_id"]]
        except:
            banca_selezionata = None
        
    # 5. Recupera ddt e righe in base al tipo (Pre-formattiamo i numeri in stringhe)
    ddt_list = []
    righe_ddt = []
    righe = []
    
    if f.get("tipo") == "FORNITURA":
        cur.execute("SELECT * FROM ddt WHERE fattura_id = %s ORDER BY data ASC, id ASC", (fattura_id,))
        ddt_list = [dict(r) for r in cur.fetchall()]
        
        cur.execute("""
            SELECT rd.* FROM righe_ddt rd
            JOIN ddt d ON rd.ddt_id = d.id
            WHERE d.fattura_id = %s ORDER BY d.data ASC, rd.id ASC
        """, (fattura_id,))
        righe_raw = cur.fetchall()
        for r in righe_raw:
            d = dict(r)
            qta = float(d.get("quantita") if d.get("quantita") is not None else 0.0)
            prz = float(d.get("prezzo") if d.get("prezzo") is not None else 0.0)
            tot_riga = qta * prz
            
            d["quantita"] = f"{qta:.2f}"
            d["prezzo"] = f"{prz:.2f}"
            d["totale"] = f"{tot_riga:.2f}"
            d["totale_float"] = tot_riga
            righe_ddt.append(d)
            righe.append(d)
    else:
        cur.execute("SELECT * FROM righe_fattura WHERE fattura_id = %s ORDER BY id ASC", (fattura_id,))
        righe_raw = cur.fetchall()
        for r in righe_raw:
            d = dict(r)
            p_raw = d.get("prezzo_unitario") if d.get("prezzo_unitario") is not None else d.get("prezzo")
            qta = float(d.get("quantita") if d.get("quantita") is not None else 0.0)
            prz = float(p_raw if p_raw is not None else 0.0)
            tot_riga = qta * prz
            
            d["quantita"] = f"{qta:.2f}"
            d["prezzo"] = f"{prz:.2f}"
            d["totale"] = f"{tot_riga:.2f}"
            d["totale_float"] = tot_riga
            righe.append(d)
            
    cur.close()

    # --- 6. GESTIONE ALIQUOTA E CALCOLO DEI TOTALI CORRETTI ---
    regime_str = str(f.get("regime_iva") or "22").strip().lower()
    
    # Rilevamento automatico dell'aliquota (Reverse Charge, Esente o Aliquota 0)
    if any(term in regime_str for term in ["0", "reverse", "esente", "non imponibile", "rc"]):
        aliquota = 0.0
    else:
        try:
            import re
            numeri = re.findall(r"\d+\.?\d*", regime_str)
            aliquota = float(numeri[0]) if numeri else 22.0
        except:
            aliquota = 22.0

    # Calcolo imponibile sommando l'importo REALE di ogni riga
    if f.get("tipo") == "FORNITURA":
        valore_imponibile = sum(r["totale_float"] for r in righe_ddt)
    else:
        valore_imponibile = sum(r["totale_float"] for r in righe)

    # Calcolo IVA e Totale
    if aliquota == 0.0:
        valore_iva = 0.0
        valore_totale = valore_imponibile
    else:
        valore_iva = valore_imponibile * (aliquota / 100.0)
        valore_totale = valore_imponibile + valore_iva

    # Sostituiamo i valori nel dizionario di fattura per il PDF
    f["totale_str"] = f"{valore_totale:.2f}"
    f["imponibile_str"] = f"{valore_imponibile:.2f}"
    f["iva_str"] = f"{valore_iva:.2f}"

    # FIX DATA: Converte la data da AAAA-MM-GG a GG/MM/AAAA se presente
    if f.get("data"):
        try:
            if hasattr(f["data"], "strftime"):
                f["data_formattata"] = f["data"].strftime("%d/%m/%Y")
            else:
                dt = datetime.strptime(str(f["data"]), "%Y-%m-%d")
                f["data_formattata"] = dt.strftime("%d/%m/%Y")
        except:
            f["data_formattata"] = f["data"]
    else:
        f["data_formattata"] = ""

    # 7. Renderizza l'HTML del template
    html = render_template(
        "pdf_fattura.html", 
        fattura=f,
        cliente=cliente,
        azienda=azienda,
        banca_selezionata=banca_selezionata,
        ddt_list=ddt_list,
        righe_ddt=righe_ddt,
        righe=righe,
        imponibile=f"{valore_imponibile:.2f}",
        iva=f"{valore_iva:.2f}",
        totale=f"{valore_totale:.2f}",
        autoprint=False
    )

    # 8. Genera il PDF in memoria
    pdf_buffer = io.BytesIO()
    try:
        pisa.CreatePDF(io.BytesIO(html.encode("utf-8")), dest=pdf_buffer)
    except Exception as pdf_error:
        return f"Errore interno del motore PDF: {pdf_error}", 500
        
    # 9. Prepara la risposta con il nome file personalizzato sicuro
    if cliente and "nome" in cliente:
        nome_cliente_pulito = str(cliente["nome"]).replace(" ", "").strip()
    else:
        nome_cliente_pulito = "generico"
        
    numero_fattura_pulito = str(f.get("numero", "")).replace("/", "-").strip() or str(fattura_id)
    filename = f"Fattura_{numero_fattura_pulito}_{nome_cliente_pulito}.pdf"
    
    response = make_response(pdf_buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    
    return response

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

# ==============================================================================
# 9. DASHBOARD & STATISTICHE
# =============================================================================

@app.route("/dashboard")
def dashboard():
    # Spostiamo l'importazione di psycopg2.extras per sicurezza, 
    # ma assumendo che sia già disponibile, lo manteniamo sicuro e accessibile
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
    
    # CORREZIONE CRUCIALE: Inizializziamo il cursore solo dopo esserci assicurati dell'importazione
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 2. Query modificata per supportare sia f.cliente che f.cliente_nome nel template HTML
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

    # 3. Calcolo dei KPI dinamici basati sulla colonna corretta del database
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

    # 4. Recupera la lista dei clienti per il menu a tendina
    try:
        cur.execute("SELECT nome FROM clienti ORDER BY nome ASC")
        clienti_lista = [r["nome"] for r in cur.fetchall()]
    except Exception:
        clienti_lista = []

    cur.close()

    # 5. Recuperiamo la password corretta direttamente dal file config.py
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

    # Conversione sicura dell'importo pagato
    if importo_pagato is not None and str(importo_pagato).strip() != "":
        try:
            importo_pagato = float(importo_pagato)
        except ValueError:
            importo_pagato = None
    else:
        importo_pagato = None

    db = get_db()
    cur = db.cursor()

    try:
        # Forza la creazione della colonna corretta se mancante
        try:
            cur.execute("ALTER TABLE fatture ADD COLUMN IF NOT EXISTS totale_pagato NUMERIC(10,2) DEFAULT 0.0;")
            db.commit()
        except Exception:
            db.rollback()

        # Aggiorna usando 'totale_pagato' (colonna reale del DB)
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
        print(f"Errore durante l'aggiornamento dello stato della fattura {fattura_id}: {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cur.close()

# ==============================================================================
# 11.NOTE 
# ==============================================================================

@app.route("/note")
def note_page():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # --- BLOCCO DI CORREZIONE AUTOMATICA SCHEMA ---
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
        print(f"Errore creazione iniziale tabella note: {e}")

    # Ora eseguiamo la query forzando il cast a TEXT di tutto per evitare conflitti
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
        # Usiamo un approccio sicuro: inseriamo i valori provando a fare il cast esplicito a TEXT
        cur.execute("""
            INSERT INTO note (titolo, contenuto, data_creazione, data_modifica) 
            VALUES ('Senza titolo', '', %s::TEXT, %s::TEXT) 
            RETURNING id
        """, (data_oggi, data_oggi))
        nuovo_id = cur.fetchone()["id"]
        db.commit()
        return jsonify({"success": True, "id": nuevo_id})
    except Exception as e:
        db.rollback()
        # Se fallisce per mismatch di tipo (colonne effettivamente TIMESTAMP nel DB fisico), usiamo NOW()
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
            print(f"Errore drastico creazione nota: {e_inner}")
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
    except Exception as e:
        db.rollback()
        # Fallback nel caso in cui data_modifica sia rimasto rigidamente un TIMESTAMP
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
            print(f"Errore drastico salvataggio nota: {e_inner}")
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
# 12. DOWNLOAD DATI CLIENTI, PRODOTTI, FATTURE
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

    # Recupera i dettagli della fattura più recente per la riga descrittiva dell'azione
    ultima_fattura = tutte_fatture[0]
    num_ultima = ultima_fattura.get('numero', '-')
    cliente_ultimo = ultima_fattura.get('cliente_nome', 'Sconosciuto')

    # Costruzione del file di testo
    output = f"Aggiunta Fattura n. {num_ultima}  {cliente_ultimo}\n"
    output += "=========================================================================================\n"
    output += "                           REPORT GENERALE BACKUP FATTURE                                \n"
    output += "=========================================================================================\n\n"
    
    for f in tutte_fatture:
        output += f"N. FATTURA: {f.get('numero', '-')} | DATA: {f.get('data', '-')}\n"
        output += f"CLIENTE:    {f.get('cliente_nome', 'Sconosciuto')}\n"
        output += f"IMPORTO:    € {f.get('totale', 0.0):.2f}\n"
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
    
    # Prendiamo tutti i clienti in ordine alfabetico per il report
    cur.execute("SELECT * FROM clienti ORDER BY nome ASC")
    clienti = cur.fetchall()
    
    # Identifichiamo l'ultimo cliente inserito in assoluto tramite ID più alto
    cur.execute("SELECT nome FROM clienti ORDER BY id DESC LIMIT 1")
    ultimo_inserito = cur.fetchone()
    cur.close()
    
    nome_ultimo = ultimo_inserito['nome'] if ultimo_inserito else '-'
    
    # Costruzione dell'output con la riga dell'azione in cima
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
    
    # Prendiamo tutti i prodotti in ordine alfabetico per il report
    cur.execute("SELECT * FROM prodotti ORDER BY nome ASC")
    prodotti = cur.fetchall()
    
    # Identifichiamo l'ultimo prodotto inserito in assoluto tramite ID più alto
    cur.execute("SELECT nome FROM prodotti ORDER BY id DESC LIMIT 1")
    ultimo_inserito = cur.fetchone()
    cur.close()
    
    nome_ultimo = ultimo_inserito['nome'] if ultimo_inserito else '-'
    
    # Costruzione dell'output con la riga dell'azione in cima
    output = f"Aggiunto Prodotto {nome_ultimo}\n"
    output += "========================================\n"
    output += "=== LISTA PRODOTTI ===\n"
    output += "========================================\n\n"
    
    for p in prodotti:
        output += f"ID: {p['id']}\nNome: {p['nome']}\nUnità di Misura: {p['unita_misura']}\nPrezzo Base: €{p['prezzo_base']:.2f}\n----------------------------------------\n"
    
    timestamp = datetime.now().strftime("%d.%m.%y_%H.%M")
    filename = f"prodotti_{timestamp}.txt"
    return Response(output, mimetype="text/plain", headers={"Content-Disposition": f"attachment;filename={filename}"})

# ==============================================================================
# 13. AVVIO APPLICAZIONE
# ==============================================================================

if __name__ == "__main__":
    app.run(debug=True)