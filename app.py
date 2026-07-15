import os
from datetime import datetime
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, g, jsonify, flash
from config import BANCHE, PASSWORD_ACCESSO

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
    """Calcola la somma imponibile basandosi sul tipo di fattura e aggiorna il totale ivato."""
    cur.execute("SELECT tipo, regime_iva FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    if not f:
        return
    tipo, regime_iva_raw = f[0], f[1] or "22"
    
    try:
        aliquota = float(regime_iva_raw)
    except:
        aliquota = 22.0

    imponibile_totale = 0.0
    
    if tipo == "FORNITURA":
        cur.execute("""
            SELECT SUM(rd.quantita * rd.prezzo) 
            FROM righe_ddt rd
            JOIN ddt d ON rd.ddt_id = d.id
            WHERE d.fattura_id = %s
        """, (fattura_id,))
        imponibile_totale = cur.fetchone()[0] or 0.0
    else:
        cur.execute("SELECT SUM(quantita * prezzo_unitario) FROM righe_fattura WHERE fattura_id = %s", (fattura_id,))
        imponibile_totale = cur.fetchone()[0] or 0.0

    totale_ivato = imponibile_totale * (1 + (aliquota / 100.0))
    cur.execute("UPDATE fatture SET totale = %s WHERE id = %s", (totale_ivato, fattura_id))


# ==============================================================================
# 3. ROTTE FATTURE (VISTA, CREAZIONE, DETTAGLIO, MODIFICA)
# ==============================================================================

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
    return render_template(
        "fatture.html", 
        fatture=elenco_fatture, 
        password_eliminazione=PASSWORD_ACCESSO
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
    
    cur.execute("SELECT * FROM prodotti ORDER BY nome ASC")
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

@app.route('/riapri_fattura/<int:id>', methods=['POST'])
def riapri_fattura(id):
    data = request.get_json()
    password_inserita = data.get("password")

    # Verifica la password usando la variabile di config
    if password_inserita != PASSWORD_ACCESSO:
        return jsonify({"success": False, "error": "Password errata"}), 403

    # Recupera la fattura
    fattura = Fattura.query.get_or_404(id)
    
    # CORREZIONE: Imposta lo stato a 'BOZZA', non a 'APERTA'
    fattura.stato = 'BOZZA' 
    
    db.session.commit()
    return jsonify({"success": True, "message": "Fattura sbloccata con successo!"})

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
    from xhtml2pdf import pisa
    from flask import make_response

    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    # 1. Recupera la fattura
    cur.execute("SELECT * FROM fatture WHERE id = %s", (fattura_id,))
    f = cur.fetchone()
    
    if not f:
        cur.close()
        return "Errore: Fattura non trovata.", 404
        
    # 2. Recupera i dati del cliente (Cessionario)
    cliente = None
    if f["cliente_id"]:
        cur.execute("SELECT * FROM clienti WHERE id = %s", (f["cliente_id"],))
        cliente = cur.fetchone()
        
    # 3. Recupera i dati dell'azienda (Cedente)
    cur.execute("SELECT * FROM azienda LIMIT 1")
    azienda = cur.fetchone()
    
    if not azienda:
        azienda = {
            "nome": "La Tua Ditta S.r.l.",
            "indirizzo": "Via Roma 123, Torino (TO)",
            "partita_iva": "IT12345678901",
            "codice_fiscale": "12345678901",
            "telefono": "+39 011 123456",
            "email": "info@lazuaditta.it"
        }
        
    # 4. Associa i dettagli della banca selezionata
    banca_selezionata = None
    elenco_banche = {
        "BPER": "BPER Banca - IT35S0538730600000004332185",
        "POSTE": "Poste Italiane - IT04B0760110200001078221247"
    }
    if f["banca_id"] in elenco_banche:
        banca_selezionata = elenco_banche[f["banca_id"]]
        
    # 5. Recupera ddt e righe in base al tipo
    ddt_list = []
    righe_ddt = []
    righe = []
    
    if f["tipo"] == "FORNITURA":
        cur.execute("SELECT * FROM ddt WHERE fattura_id = %s ORDER BY data ASC, id ASC", (fattura_id,))
        ddt_list = cur.fetchall()
        
        cur.execute("""
            SELECT rd.* FROM righe_ddt rd
            JOIN ddt d ON rd.ddt_id = d.id
            WHERE d.fattura_id = %s ORDER BY d.data ASC, rd.id ASC
        """, (fattura_id,))
        righe_raw = cur.fetchall()
        for r in righe_raw:
            d = dict(r)
            d["totale"] = d["quantita"] * d["prezzo"]
            righe_ddt.append(d)
    else:
        cur.execute("SELECT * FROM righe_fattura WHERE fattura_id = %s ORDER BY id ASC", (fattura_id,))
        righe_raw = cur.fetchall()
        for r in righe_raw:
            d = dict(r)
            d["prezzo"] = d.get("prezzo_unitario", d.get("prezzo", 0.0))
            d["totale"] = d["quantita"] * d["prezzo"]
            righe.append(d)
            
    cur.close()

    # 6. Calcoli economici
    valore_totale = float(f.get("totale", 0.0) or 0.0)
    try:
        aliquota = float(f["regime_iva"])
    except:
        aliquota = 22.0
        
    valore_imponibile = valore_totale / (1 + (aliquota / 100.0))
    valore_iva = valore_totale - valore_imponibile

    # 7. Renderizza l'HTML del template
    html = render_template(
        "pdf_fattura.html", 
        fattura=dict(f),
        cliente=cliente,
        azienda=azienda,
        banca_selezionata=banca_selezionata,
        ddt_list=ddt_list,
        righe_ddt=righe_ddt,
        righe=righe,
        imponibile=valore_imponibile,
        iva=valore_iva,
        totale=valore_totale,
        autoprint=False
    )

    # 8. Genera il PDF in memoria
    pdf_buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(io.BytesIO(html.encode("utf-8")), dest=pdf_buffer)
    
    if pisa_status.err:
        return "Errore durante la generazione del PDF", 500
        
    # 9. Prepara la risposta con il nome file personalizzato
    nome_cliente_pulito = (cliente["nome"] if cliente else "generico").replace(" ", "").strip()
    numero_fattura_pulito = str(f["numero"]).replace("/", "-").strip() if f["numero"] else str(fattura_id)
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
# ==============================================================================

@app.route("/dashboard")
def dashboard():
    db = get_db()
    cur = db.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    cur.execute("""
        SELECT 
            COALESCE(SUM(totale), 0) as totale_fatturato, 
            COALESCE(SUM(CASE WHEN stato_pagamento='Pagata' THEN totale ELSE 0 END), 0) as totale_incassato, 
            COALESCE(SUM(CASE WHEN stato_pagamento='In attesa' THEN totale ELSE 0 END), 0) as totale_attesa, 
            COALESCE(SUM(CASE WHEN stato_pagamento='Non pagata' THEN totale ELSE 0 END), 0) as totale_non_pagato 
        FROM fatture WHERE stato='CHIUSA'
    """)
    stats = cur.fetchone()
    
    cur.execute("""
        SELECT 
            COUNT(*) as totale_invii, 
            COUNT(CASE WHEN stato='BOZZA' THEN 1 END) as bozze, 
            COUNT(CASE WHEN stato='CHIUSA' THEN 1 END) as chiuse 
        FROM fatture
    """)
    conteggi = cur.fetchone()
    
    # query protetta da conversioni di tipo data errate in PostgreSQL
    cur.execute("""
        SELECT 
            TO_CHAR(data::date, 'YYYY-MM') as mese, 
            SUM(totale) as totale 
        FROM fatture 
        WHERE stato='CHIUSA' AND data IS NOT NULL
        GROUP BY TO_CHAR(data::date, 'YYYY-MM') 
        ORDER BY mese DESC 
        LIMIT 6
    """)
    trend_mensile = cur.fetchall()
    
    cur.close()
    return render_template(
        "dashboard.html", 
        stats=stats, 
        conteggi=conteggi, 
        trend_mensile=trend_mensile
    )


# ==============================================================================
# 10. AVVIO APPLICAZIONE
# ==============================================================================

if __name__ == "__main__":
    app.run(debug=True)