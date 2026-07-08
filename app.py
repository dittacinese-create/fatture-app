from flask import Flask, render_template, request, redirect, make_response, send_file, jsonify, session
from database import init_db, get_db, return_db
from config import AZIENDA, PASSWORD_ACCESSO
from functools import wraps
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chiave-segreta-fatture-2026")

@app.context_processor
def inject_request():
    return dict(request=request)

with app.app_context():
    init_db()

# =========================
# LOGIN
# =========================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect("/fatture")
    errore = False
    if request.method == "POST":
        if request.form.get("password") == PASSWORD_ACCESSO:
            session["logged_in"] = True
            return redirect("/fatture")
        errore = True
    return render_template("login.html", errore=errore)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# =========================
# CLIENTI
# =========================

@app.route("/")
@login_required
def home():
    return redirect("/fatture")

@app.route("/clienti")
@login_required
def clienti():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM clienti ORDER BY nome ASC")
    clienti = cur.fetchall()
    cur.close()
    return_db(db)
    return render_template("clienti.html", clienti=clienti)

@app.route("/add", methods=["POST"])
@login_required
def add():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO clienti (nome, indirizzo, partita_iva, codice_fiscale, codice_sdi, pec)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (
        request.form["nome"],
        request.form["indirizzo"],
        request.form["partita_iva"],
        request.form["codice_fiscale"],
        request.form["codice_sdi"],
        request.form["pec"]
    ))
    db.commit()
    cur.close()
    return_db(db)
    return redirect("/clienti")

@app.route("/modifica_cliente/<int:id>", methods=["POST"])
@login_required
def modifica_cliente(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE clienti SET
            nome = %s,
            indirizzo = %s,
            partita_iva = %s,
            codice_fiscale = %s,
            codice_sdi = %s,
            pec = %s
        WHERE id = %s
    """, (
        request.form["nome"],
        request.form["indirizzo"],
        request.form["partita_iva"],
        request.form["codice_fiscale"],
        request.form["codice_sdi"],
        request.form["pec"],
        id
    ))
    db.commit()
    cur.close()
    return_db(db)
    return jsonify({"success": True})

@app.route("/delete_cliente/<int:id>")
@login_required
def delete_cliente(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM clienti WHERE id = %s", (id,))
    db.commit()
    cur.close()
    return_db(db)
    return redirect("/clienti")

# =========================
# PRODOTTI
# =========================

@app.route("/prodotti")
@login_required
def prodotti():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM prodotti ORDER BY nome ASC")
    prodotti = cur.fetchall()
    cur.close()
    return_db(db)
    return render_template("prodotti.html", prodotti=prodotti)

@app.route("/add_prodotto_ajax", methods=["POST"])
@login_required
def add_prodotto_ajax():
    data = request.get_json()
    nome = data.get("nome", "").strip()
    prezzo_base = data.get("prezzo_base", 0)
    unita_misura = data.get("unita_misura", "mq")
    if not nome or prezzo_base <= 0:
        return jsonify({"success": False})
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO prodotti (nome, prezzo_base, unita_misura) VALUES (%s, %s, %s) RETURNING id",
        (nome, prezzo_base, unita_misura)
    )
    nuovo_id = cur.fetchone()["id"]
    db.commit()
    cur.close()
    return_db(db)
    return jsonify({"success": True, "id": nuovo_id})

@app.route("/add_prodotto", methods=["POST"])
@login_required
def add_prodotto():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO prodotti (nome, prezzo_base, unita_misura)
        VALUES (%s, %s, %s)
    """, (
        request.form["nome"],
        float(request.form["prezzo_base"]),
        request.form.get("unita_misura", "mq")
    ))
    db.commit()
    cur.close()
    return_db(db)
    return redirect("/prodotti")

@app.route("/modifica_prodotto/<int:id>", methods=["POST"])
@login_required
def modifica_prodotto(id):
    data = request.get_json()
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE prodotti SET
            nome = %s,
            prezzo_base = %s,
            unita_misura = %s
        WHERE id = %s
    """, (
        data.get("nome"),
        float(data.get("prezzo_base", 0)),
        data.get("unita_misura", "mq"),
        id
    ))
    db.commit()
    cur.close()
    return_db(db)
    return jsonify({"success": True})

@app.route("/delete_prodotto/<int:id>")
@login_required
def delete_prodotto(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM prodotti WHERE id = %s", (id,))
    db.commit()
    cur.close()
    return_db(db)
    return redirect("/prodotti")

# =========================
# FATTURE
# =========================

@app.route("/fatture")
@login_required
def fatture():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT f.*, c.nome AS cliente_nome
        FROM fatture f
        JOIN clienti c ON c.id = f.cliente_id
        ORDER BY f.numero DESC
    """)
    fatture = cur.fetchall()
    cur.close()
    return_db(db)
    return render_template("fatture.html", fatture=fatture)

@app.route("/nuova_fattura")
@login_required
def nuova_fattura():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM clienti ORDER BY nome")
    clienti = cur.fetchall()

    # Genera numero fattura automatico — solo numero progressivo
    cur.execute("SELECT numero FROM fatture ORDER BY id DESC LIMIT 1")
    ultima = cur.fetchone()
    if ultima:
        try:
            # Funziona sia con "2026/043" che con "43"
            ultimo_num = int(ultima["numero"].split("/")[-1])
            prossimo_numero = str(ultimo_num + 1)
        except:
            prossimo_numero = "1"
    else:
        prossimo_numero = "1"

    from datetime import datetime, date
    oggi = date.today().isoformat()
    cur.close()
    return_db(db)
    return render_template("nuova_fattura.html", clienti=clienti, prossimo_numero=prossimo_numero, oggi=oggi)

@app.route("/add_fattura", methods=["POST"])
@login_required
def add_fattura():
    db = get_db()
    cur = db.cursor()
    iban = request.form.get("iban")
    if not iban:
        cur.close()
        return_db(db)
        return "IBAN mancante", 400
    cur.execute("""
        INSERT INTO fatture
        (numero, data, cliente_id, tipo, regime_iva, stato, iban)
        VALUES (%s, %s, %s, %s, %s, 'BOZZA', %s)
        RETURNING id
    """, (
        request.form["numero"],
        request.form["data"],
        request.form["cliente_id"],
        request.form["tipo"],
        request.form.get("regime_iva", "22"),
        iban
    ))
    fattura_id = cur.fetchone()["id"]
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{fattura_id}")

@app.route("/aggiorna_testata/<int:id>", methods=["POST"])
@login_required
def aggiorna_testata(id):
    data = request.get_json()
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE fatture SET numero=%s, data=%s WHERE id=%s
    """, (data.get("numero"), data.get("data"), id))
    db.commit()
    cur.close()
    return_db(db)
    return jsonify({"success": True})


@app.route("/aggiorna_fattura_ajax/<int:id>", methods=["POST"])
@login_required
def aggiorna_fattura_ajax(id):
    data = request.get_json()
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE fatture SET
            stato_pagamento = %s,
            data_scadenza = %s,
            data_pagamento = %s,
            note = %s
        WHERE id = %s
    """, (
        data.get("stato_pagamento", "Non pagata"),
        data.get("data_scadenza") or None,
        data.get("data_pagamento") or None,
        data.get("note") or None,
        id
    ))
    db.commit()
    cur.close()
    return_db(db)
    return jsonify({"success": True})

@app.route("/aggiorna_fattura/<int:id>", methods=["POST"])
@login_required
def aggiorna_fattura(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE fatture SET
            stato_pagamento = %s,
            data_scadenza = %s,
            data_pagamento = %s,
            note = %s
        WHERE id = %s
    """, (
        request.form.get("stato_pagamento", "Non pagata"),
        request.form.get("data_scadenza") or None,
        request.form.get("data_pagamento") or None,
        request.form.get("note") or None,
        id
    ))
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{id}")

@app.route("/delete_fattura/<int:id>")
@login_required
def delete_fattura(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM righe_fattura WHERE fattura_id=%s", (id,))
    cur.execute("""
        DELETE FROM righe_ddt WHERE ddt_id IN (
            SELECT id FROM ddt WHERE fattura_id=%s
        )
    """, (id,))
    cur.execute("DELETE FROM ddt WHERE fattura_id=%s", (id,))
    cur.execute("DELETE FROM fatture WHERE id=%s", (id,))
    db.commit()
    cur.close()
    return_db(db)
    return redirect("/fatture")

@app.route("/chiudi_fattura/<int:id>")
@login_required
def chiudi_fattura(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM fatture WHERE id=%s", (id,))
    fattura = cur.fetchone()
    cur.execute("SELECT * FROM righe_fattura WHERE fattura_id=%s", (id,))
    righe = cur.fetchall()
    cur.execute("""
        SELECT r.* FROM righe_ddt r
        JOIN ddt d ON d.id = r.ddt_id
        WHERE d.fattura_id = %s
    """, (id,))
    righe_ddt = cur.fetchall()
    if fattura["tipo"] == "FORNITURA":
        imponibile = round(sum(r["totale"] for r in righe_ddt), 2)
    else:
        imponibile = round(sum(r["totale"] for r in righe), 2)
    if fattura["regime_iva"] == "22":
        iva = round(imponibile * 0.22, 2)
        totale = round(imponibile + iva, 2)
    else:
        iva = 0
        totale = imponibile
    cur.execute("UPDATE fatture SET stato='CHIUSA', totale=%s WHERE id=%s", (totale, id))
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{id}")

@app.route("/fattura/<int:id>")
@login_required
def fattura_dettaglio(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT f.*, c.nome AS cliente_nome, c.indirizzo,
               c.partita_iva, c.codice_fiscale, c.codice_sdi, c.pec
        FROM fatture f
        JOIN clienti c ON c.id = f.cliente_id
        WHERE f.id = %s
    """, (id,))
    fattura = cur.fetchone()
    cur.execute("SELECT * FROM righe_fattura WHERE fattura_id=%s ORDER BY id ASC", (id,))
    righe = cur.fetchall()
    cur.execute("SELECT * FROM prodotti ORDER BY nome", )
    prodotti = cur.fetchall()
    cur.execute("SELECT * FROM ddt WHERE fattura_id=%s ORDER BY id ASC", (id,))
    ddt_list = cur.fetchall()
    cur.execute("""
        SELECT r.* FROM righe_ddt r
        JOIN ddt d ON d.id = r.ddt_id
        WHERE d.fattura_id = %s
    """, (id,))
    righe_ddt = cur.fetchall()
    cur.close()
    return_db(db)
    if fattura["tipo"] == "FORNITURA":
        imponibile = sum(r["totale"] for r in righe_ddt)
    else:
        imponibile = sum(r["totale"] for r in righe)
    if fattura["regime_iva"] == "22":
        iva = round(imponibile * 0.22, 2)
        totale = round(imponibile + iva, 2)
        nota_iva = None
    else:
        iva = 0
        totale = imponibile
        nota_iva = "Operazione soggetta a Reverse Charge – IVA assolta dal committente (art. 17 c. 6/A DPR 633/72)"
    return render_template(
        "fattura_dettaglio.html",
        fattura=fattura, righe=righe, prodotti=prodotti,
        ddt_list=ddt_list, righe_ddt=righe_ddt,
        imponibile=imponibile, iva=iva, totale=totale, nota_iva=nota_iva
    )

# =========================
# RIGHE FATTURA
# =========================

@app.route("/add_riga", methods=["POST"])
@login_required
def add_riga():
    db = get_db()
    cur = db.cursor()
    fattura_id = request.form["fattura_id"]
    q = float(request.form["quantita"])
    prezzo = float(request.form["prezzo"])
    totale = round(q * prezzo, 2)
    cur.execute("""
        INSERT INTO righe_fattura
        (fattura_id, descrizione, quantita, unita_misura, prezzo, totale)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (fattura_id, request.form["descrizione"], q, request.form["unita_misura"], prezzo, totale))
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{fattura_id}")

@app.route("/delete_riga_fattura/<int:id>/<int:fattura_id>")
@login_required
def delete_riga_fattura(id, fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM righe_fattura WHERE id=%s", (id,))
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{fattura_id}")

@app.route("/delete_riga_ddt/<int:id>/<int:fattura_id>")
@login_required
def delete_riga_ddt(id, fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM righe_ddt WHERE id=%s", (id,))
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{fattura_id}")

# =========================
# DDT
# =========================

@app.route("/add_ddt", methods=["POST"])
@login_required
def add_ddt():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO ddt (fattura_id, numero, data) VALUES (%s, %s, %s)
    """, (request.form["fattura_id"], request.form["numero"], request.form["data"]))
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{request.form['fattura_id']}")

@app.route("/delete_ddt/<int:ddt_id>/<int:fattura_id>")
@login_required
def delete_ddt(ddt_id, fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM righe_ddt WHERE ddt_id=%s", (ddt_id,))
    cur.execute("DELETE FROM ddt WHERE id=%s", (ddt_id,))
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{fattura_id}")

@app.route("/add_riga_prodotto", methods=["POST"])
@login_required
def add_riga_prodotto():
    db = get_db()
    cur = db.cursor()
    fattura_id = request.form["fattura_id"]
    ddt_id = request.form["ddt_id"]
    cur.execute("SELECT * FROM prodotti WHERE id=%s", (request.form["prodotto_id"],))
    prodotto = cur.fetchone()
    quantita = float(request.form["quantita"])
    prezzo_override = request.form.get("prezzo_override")
    prezzo = float(prezzo_override) if prezzo_override else prodotto["prezzo_base"]
    totale = round(quantita * prezzo, 2)
    cur.execute("""
        INSERT INTO righe_ddt
        (ddt_id, prodotto_id, descrizione, quantita, prezzo, unita_misura, totale)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (ddt_id, prodotto["id"], prodotto["nome"], quantita, prezzo, prodotto["unita_misura"], totale))
    db.commit()
    cur.close()
    return_db(db)
    return redirect(f"/fattura/{fattura_id}")

# =========================
# NOTE
# =========================

@app.route("/note")
@login_required
def note():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM note ORDER BY data_modifica DESC, id DESC")
    note = cur.fetchall()
    cur.close()
    return_db(db)
    return render_template("note.html", note=note)

@app.route("/nuova_nota", methods=["POST"])
@login_required
def nuova_nota():
    db = get_db()
    cur = db.cursor()
    cur.execute("INSERT INTO note (titolo, contenuto) VALUES ('', '') RETURNING id")
    nuovo_id = cur.fetchone()["id"]
    db.commit()
    cur.close()
    return_db(db)
    return jsonify({"success": True, "id": nuovo_id})

@app.route("/nota/<int:id>")
@login_required
def get_nota(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM note WHERE id=%s", (id,))
    nota = cur.fetchone()
    cur.close()
    return_db(db)
    return jsonify({"titolo": nota["titolo"], "contenuto": nota["contenuto"]})

@app.route("/salva_nota/<int:id>", methods=["POST"])
@login_required
def salva_nota(id):
    data = request.get_json()
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        UPDATE note SET titolo=%s, contenuto=%s, data_modifica=CURRENT_DATE
        WHERE id=%s
    """, (data.get("titolo", ""), data.get("contenuto", ""), id))
    db.commit()
    cur.close()
    return_db(db)
    return jsonify({"success": True})

@app.route("/elimina_nota/<int:id>", methods=["POST"])
@login_required
def elimina_nota(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM note WHERE id=%s", (id,))
    db.commit()
    cur.close()
    return_db(db)
    return jsonify({"success": True})


# =========================
# PDF
# =========================

@app.route("/pdf/<int:id>")
@login_required
def pdf(id):
    from xhtml2pdf import pisa
    import io
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT f.*, c.nome AS cliente_nome, c.indirizzo AS cliente_indirizzo,
               c.partita_iva AS cliente_piva, c.codice_fiscale AS cliente_cf,
               c.codice_sdi AS cliente_sdi, c.pec AS cliente_pec
        FROM fatture f
        JOIN clienti c ON c.id = f.cliente_id
        WHERE f.id = %s
    """, (id,))
    fattura = cur.fetchone()
    cur.execute("SELECT * FROM righe_fattura WHERE fattura_id=%s", (id,))
    righe = cur.fetchall()
    cur.execute("SELECT * FROM ddt WHERE fattura_id=%s ORDER BY id", (id,))
    ddt_list = cur.fetchall()
    cur.execute("""
        SELECT r.* FROM righe_ddt r
        JOIN ddt d ON d.id = r.ddt_id
        WHERE d.fattura_id = %s
    """, (id,))
    righe_ddt = cur.fetchall()
    cur.close()
    return_db(db)
    righe_pdf = righe_ddt if fattura["tipo"] == "FORNITURA" else righe
    imponibile = round(sum(r["totale"] for r in righe_pdf), 2)
    if fattura["regime_iva"] == "22":
        iva = round(imponibile * 0.22, 2)
        totale = round(imponibile + iva, 2)
    else:
        iva = 0
        totale = imponibile
    html_content = render_template(
        "pdf_fattura.html",
        fattura=fattura, azienda=AZIENDA,
        righe=righe, ddt_list=ddt_list, righe_ddt=righe_ddt,
        imponibile=imponibile, iva=iva, totale=totale
    )
    buffer = io.BytesIO()
    pisa.CreatePDF(html_content, dest=buffer)
    buffer.seek(0)
    numero_safe = fattura['numero'].replace('/', '_')
    response = make_response(buffer.read())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename=fattura_{numero_safe}.pdf"
    return response

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(debug=False, host="0.0.0.0", port=port)