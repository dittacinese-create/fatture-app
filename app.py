from flask import Flask, render_template, request, redirect, make_response, send_file, jsonify
from database import init_db, get_db
from config import AZIENDA
import os

app = Flask(__name__)

@app.context_processor
def inject_request():
    return dict(request=request)

with app.app_context():
    init_db()

# =========================
# CLIENTI
# =========================

@app.route("/")
def home():
    return redirect("/fatture")

@app.route("/clienti")
def clienti():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM clienti ORDER BY id DESC")
    clienti = cur.fetchall()
    cur.close()
    db.close()
    return render_template("clienti.html", clienti=clienti)


@app.route("/add", methods=["POST"])
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
    db.close()
    return redirect("/clienti")


@app.route("/delete_cliente/<int:id>")
def delete_cliente(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM clienti WHERE id = %s", (id,))
    db.commit()
    cur.close()
    db.close()
    return redirect("/clienti")


# =========================
# PRODOTTI
# =========================

@app.route("/prodotti")
def prodotti():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM prodotti ORDER BY id DESC")
    prodotti = cur.fetchall()
    cur.close()
    db.close()
    return render_template("prodotti.html", prodotti=prodotti)


@app.route("/add_prodotto_ajax", methods=["POST"])
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
    db.close()
    return jsonify({"success": True, "id": nuovo_id})


@app.route("/add_prodotto", methods=["POST"])
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
    db.close()
    return redirect("/prodotti")


@app.route("/delete_prodotto/<int:id>")
def delete_prodotto(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM prodotti WHERE id = %s", (id,))
    db.commit()
    cur.close()
    db.close()
    return redirect("/prodotti")


# =========================
# FATTURE LISTA
# =========================

@app.route("/fatture")
def fatture():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        SELECT f.*, c.nome AS cliente_nome
        FROM fatture f
        JOIN clienti c ON c.id = f.cliente_id
        ORDER BY f.id DESC
    """)
    fatture = cur.fetchall()
    cur.close()
    db.close()
    return render_template("fatture.html", fatture=fatture)


@app.route("/nuova_fattura")
def nuova_fattura():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM clienti ORDER BY nome")
    clienti = cur.fetchall()
    cur.close()
    db.close()
    return render_template("nuova_fattura.html", clienti=clienti)


# =========================
# CREA FATTURA
# =========================

@app.route("/add_fattura", methods=["POST"])
def add_fattura():
    db = get_db()
    cur = db.cursor()

    iban = request.form.get("iban")
    if not iban:
        cur.close()
        db.close()
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
    db.close()

    return redirect(f"/fattura/{fattura_id}")


@app.route("/delete_fattura/<int:id>")
def delete_fattura(id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM righe_fattura WHERE fattura_id=%s", (id,))
    cur.execute("""
        DELETE FROM righe_ddt
        WHERE ddt_id IN (
            SELECT id FROM ddt WHERE fattura_id=%s
        )
    """, (id,))
    cur.execute("DELETE FROM ddt WHERE fattura_id=%s", (id,))
    cur.execute("DELETE FROM fatture WHERE id=%s", (id,))
    db.commit()
    cur.close()
    db.close()
    return redirect("/fatture")


# =========================
# CHIUDI FATTURA
# =========================

@app.route("/chiudi_fattura/<int:id>")
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

    cur.execute("""
        UPDATE fatture SET stato='CHIUSA', totale=%s
        WHERE id=%s
    """, (totale, id))
    db.commit()
    cur.close()
    db.close()
    return redirect(f"/fattura/{id}")


# =========================
# DETTAGLIO FATTURA
# =========================

@app.route("/fattura/<int:id>")
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

    cur.execute("""
        SELECT * FROM righe_fattura WHERE fattura_id=%s
        ORDER BY id ASC
    """, (id,))
    righe = cur.fetchall()

    cur.execute("SELECT * FROM prodotti ORDER BY nome")
    prodotti = cur.fetchall()

    cur.execute("""
        SELECT * FROM ddt WHERE fattura_id=%s
        ORDER BY id ASC
    """, (id,))
    ddt_list = cur.fetchall()

    cur.execute("""
        SELECT r.*
        FROM righe_ddt r
        JOIN ddt d ON d.id = r.ddt_id
        WHERE d.fattura_id = %s
    """, (id,))
    righe_ddt = cur.fetchall()

    cur.close()
    db.close()

    if fattura["tipo"] == "FORNITURA":
        imponibile = sum(r["totale"] for r in righe_ddt)
    else:
        imponibile = sum(r["totale"] for r in righe)

    regime_iva = fattura["regime_iva"]
    if regime_iva == "22":
        iva = round(imponibile * 0.22, 2)
        totale = round(imponibile + iva, 2)
        nota_iva = None
    else:
        iva = 0
        totale = imponibile
        nota_iva = "Operazione soggetta a Reverse Charge – IVA assolta dal committente (art. 17 c. 6/A DPR 633/72)"

    return render_template(
        "fattura_dettaglio.html",
        fattura=fattura,
        righe=righe,
        prodotti=prodotti,
        ddt_list=ddt_list,
        righe_ddt=righe_ddt,
        imponibile=imponibile,
        iva=iva,
        totale=totale,
        nota_iva=nota_iva
    )


# =========================
# ADD RIGHE FATTURA
# =========================

@app.route("/add_riga", methods=["POST"])
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
    """, (
        fattura_id,
        request.form["descrizione"],
        q,
        request.form["unita_misura"],
        prezzo,
        totale
    ))
    db.commit()
    cur.close()
    db.close()
    return redirect(f"/fattura/{fattura_id}")


# =========================
# DELETE RIGHE FATTURA
# =========================

@app.route("/delete_riga_fattura/<int:id>/<int:fattura_id>")
def delete_riga_fattura(id, fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM righe_fattura WHERE id=%s", (id,))
    db.commit()
    cur.close()
    db.close()
    return redirect(f"/fattura/{fattura_id}")


# =========================
# DELETE RIGHE DDT
# =========================

@app.route("/delete_riga_ddt/<int:id>/<int:fattura_id>")
def delete_riga_ddt(id, fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM righe_ddt WHERE id=%s", (id,))
    db.commit()
    cur.close()
    db.close()
    return redirect(f"/fattura/{fattura_id}")


# =========================
# DDT
# =========================

@app.route("/add_ddt", methods=["POST"])
def add_ddt():
    db = get_db()
    cur = db.cursor()
    cur.execute("""
        INSERT INTO ddt (fattura_id, numero, data)
        VALUES (%s, %s, %s)
    """, (
        request.form["fattura_id"],
        request.form["numero"],
        request.form["data"]
    ))
    db.commit()
    cur.close()
    db.close()
    return redirect(f"/fattura/{request.form['fattura_id']}")


@app.route("/delete_ddt/<int:ddt_id>/<int:fattura_id>")
def delete_ddt(ddt_id, fattura_id):
    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM righe_ddt WHERE ddt_id=%s", (ddt_id,))
    cur.execute("DELETE FROM ddt WHERE id=%s", (ddt_id,))
    db.commit()
    cur.close()
    db.close()
    return redirect(f"/fattura/{fattura_id}")


# =========================
# RIGHE DDT
# =========================

@app.route("/add_riga_prodotto", methods=["POST"])
def add_riga_prodotto():
    db = get_db()
    cur = db.cursor()

    fattura_id = request.form["fattura_id"]
    ddt_id = request.form["ddt_id"]

    cur.execute(
        "SELECT * FROM prodotti WHERE id=%s",
        (request.form["prodotto_id"],)
    )
    prodotto = cur.fetchone()

    quantita = float(request.form["quantita"])
    # Prezzo: usa quello modificato dall'utente se presente, altrimenti il prezzo base
    prezzo_override = request.form.get("prezzo_override")
    prezzo = float(prezzo_override) if prezzo_override else prodotto["prezzo_base"]
    totale = round(quantita * prezzo, 2)

    cur.execute("""
        INSERT INTO righe_ddt
        (ddt_id, prodotto_id, descrizione, quantita, prezzo, unita_misura, totale)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        ddt_id,
        prodotto["id"],
        prodotto["nome"],
        quantita,
        prezzo,
        prodotto["unita_misura"],
        totale
    ))

    db.commit()
    cur.close()
    db.close()

    return redirect(f"/fattura/{fattura_id}")


# =========================
# PDF
# =========================

@app.route("/pdf/<int:id>")
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
    db.close()

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
        fattura=fattura,
        azienda=AZIENDA,
        righe=righe,
        ddt_list=ddt_list,
        righe_ddt=righe_ddt,
        imponibile=imponibile,
        iva=iva,
        totale=totale
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