import os
import psycopg2
from psycopg2.extras import RealDictCursor

# Stringa di connessione a PostgreSQL di Render
DATABASE_URL = "postgresql://fatture_db_user:7jo0JrHXuBmiFOkNiyWOhPxth1LA3Y9L@dpg-d9bidafaqgkc739f0jug-a.oregon-postgres.render.com/fatture_db"

STORICO_FATTURE = [
    {"n_fattura": 1, "cliente": "Teknogreen", "data": "2026-01-10", "importo": 10370.00, "note": "Cirie", "stato": "Pagato", "data_pagamento": "2026-01-22"},
    {"n_fattura": 2, "cliente": "Morina", "data": "2026-01-31", "importo": 10455.89, "note": "Fornitura", "stato": "Pagato", "data_pagamento": "2026-04-07"},
    {"n_fattura": 3, "cliente": "Saxso", "data": "2026-03-05", "importo": 15053.09, "note": "Fornitura", "stato": "Pagato", "data_pagamento": "2026-04-22"},
    {"n_fattura": 4, "cliente": "Teknogreen", "data": "2026-03-16", "importo": 3000.00, "note": "Cirie", "stato": "Pagato", "data_pagamento": "2026-04-01"},
    {"n_fattura": 5, "cliente": "Teknogreen", "data": "2026-03-16", "importo": 18666.00, "note": "Cirie", "stato": "Pagato", "data_pagamento": "2026-04-01"},
    {"n_fattura": 6, "cliente": "Cogeis", "data": "2026-03-16", "importo": 3200.00, "note": "Donnas", "stato": "Pagato", "data_pagamento": "2026-04-22"},
    {"n_fattura": 7, "cliente": "Stone", "data": "2026-03-20", "importo": 2550.00, "note": "La Spezia", "stato": "Pagato", "data_pagamento": "2026-05-12"},
    {"n_fattura": 8, "cliente": "Carnio Carlo", "data": "2026-03-26", "importo": 2000.00, "note": "Asti", "stato": "Pagato", "data_pagamento": "2026-04-02"},
    {"n_fattura": 9, "cliente": "Saxso", "data": "2026-03-31", "importo": 28085.86, "note": "Fornitura", "stato": "Pagato", "data_pagamento": "2026-07-03"},
    {"n_fattura": 10, "cliente": "Benedetto Silvio", "data": "2026-03-31", "importo": 7288.77, "note": "Fornitura", "stato": "Pagato", "data_pagamento": "2026-07-06"},
    {"n_fattura": 11, "cliente": "Alpe", "data": "2026-04-14", "importo": 2248.00, "note": "Santa Vittoria d'Alba", "stato": "Pagato", "data_pagamento": "2026-06-08"},
    {"n_fattura": 12, "cliente": "Alpe", "data": "2026-04-14", "importo": 5209.00, "note": "Carmagnola", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 13, "cliente": "Alpe", "data": "2026-04-14", "importo": 540.00, "note": "Torino", "stato": "Pagato", "data_pagamento": "2026-06-08"},
    {"n_fattura": 14, "cliente": "Alpe", "data": "2026-04-17", "importo": 3796.00, "note": "Saldo", "stato": "Pagato", "data_pagamento": "2026-06-25"},
    {"n_fattura": 15, "cliente": "UBM costruzioni", "data": "2026-04-20", "importo": 5301.94, "note": "Colico", "stato": "Pagato", "data_pagamento": "2026-06-01"},
    {"n_fattura": 16, "cliente": "Vai Gualtiero", "data": "2026-04-20", "importo": 1600.00, "note": "Sciolze", "stato": "Pagato", "data_pagamento": "2026-04-28"},
    {"n_fattura": 17, "cliente": "Cogeis", "data": "2026-04-21", "importo": 20000.00, "note": "Cantoira", "stato": "Pagato", "data_pagamento": "2026-05-05"},
    {"n_fattura": 18, "cliente": "Saxso", "data": "2026-04-30", "importo": 22709.32, "note": "Fornitura", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 19, "cliente": "Benedetto Silvio", "data": "2026-04-30", "importo": 5559.66, "note": "Fornitura", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 20, "cliente": "Cogeis", "data": "2026-05-04", "importo": 12900.00, "note": "Cantoira", "stato": "Pagato", "data_pagamento": "2026-06-22"},
    {"n_fattura": 21, "cliente": "Fratelli Bottano", "data": "2026-05-11", "importo": 2135.00, "note": "Fornitura", "stato": "Pagato", "data_pagamento": "2026-06-19"},
    {"n_fattura": 22, "cliente": "Fratelli Bottano", "data": "2026-05-11", "importo": 6658.00, "note": "Barge", "stato": "Pagato", "data_pagamento": "2026-06-19"},
    {"n_fattura": 23, "cliente": "Salvi", "data": "2026-05-15", "importo": 3000.00, "note": "Corsico", "stato": "Pagato", "data_pagamento": "2026-05-19"},
    {"n_fattura": 25, "cliente": "Teknogreen", "data": "2026-05-18", "importo": 18666.00, "note": "Cirie", "stato": "Pagato", "data_pagamento": "2026-06-04"},
    {"n_fattura": 27, "cliente": "Godino Scavi", "data": "2026-05-15", "importo": 7414.00, "note": "Varisella", "stato": "Pagato", "data_pagamento": "2026-05-27"},
    {"n_fattura": 28, "cliente": "Idea Edilizia", "data": "2026-05-19", "importo": 5001.39, "note": "Fornitura", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 29, "cliente": "Kostruttiva", "data": "2026-05-19", "importo": 28421.73, "note": "Fornitura", "stato": "Pagato", "data_pagamento": "2026-07-13"},
    {"n_fattura": 30, "cliente": "Saxso", "data": "2026-05-29", "importo": 29933.43, "note": "Fornitura", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 31, "cliente": "Benedetto Silvio", "data": "2026-05-29", "importo": 8511.82, "note": "Fornitura", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 33, "cliente": "Cogeis", "data": "2026-06-05", "importo": 15830.10, "note": "Cantoira", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 35, "cliente": "Nordscavi", "data": "2026-06-05", "importo": 1357.00, "note": "Fenestrelle", "stato": "Pagato", "data_pagamento": "2026-06-29"},
    {"n_fattura": 36, "cliente": "La Passatore", "data": "2026-06-10", "importo": 15000.00, "note": "Elva", "stato": "Pagato", "data_pagamento": "2026-06-25"},
    {"n_fattura": 37, "cliente": "Alpe", "data": "2026-06-15", "importo": 8756.00, "note": "Cantieri", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 38, "cliente": "Icose", "data": "2026-06-19", "importo": 1680.00, "note": "Ormea", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 39, "cliente": "Costruire", "data": "2026-06-19", "importo": 10736.00, "note": "Alba Altavilla", "stato": "Pagato", "data_pagamento": "2026-07-14"},
    {"n_fattura": 40, "cliente": "Guarnero", "data": "2026-06-28", "importo": 2032.75, "note": "Distacco", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 41, "cliente": "Guarnero", "data": "2026-06-28", "importo": 4680.00, "note": "Falchera", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 42, "cliente": "Saxso", "data": "2026-06-30", "importo": 43745.78, "note": "Fornitura", "stato": "Non Pagato", "data_pagamento": None},
    {"n_fattura": 43, "cliente": "Benedetto Silvio", "data": "2026-06-30", "importo": 3067.93, "note": "Fornitura", "stato": "Non Pagato", "data_pagamento": None}
]

def importa():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    print("🚀 Inizio importazione dello storico...")
    
    try:
        for f in STORICO_FATTURE:
            # Controlla se il cliente esiste, altrimenti lo crea
            cur.execute("SELECT id FROM clienti WHERE nome = %s;", (f["cliente"],))
            res_cliente = cur.fetchone()
            
            if res_cliente:
                cliente_id = res_cliente["id"]
            else:
                cur.execute(
                    "INSERT INTO clienti (nome) VALUES (%s) RETURNING id;",
                    (f["cliente"],)
                )
                cliente_id = cur.fetchone()["id"]
                print(f"👤 Cliente '{f['cliente']}' creato automaticamente.")

            # Inserisce la fattura adattando i campi al database reale.
            # Nota: Sostituito 'DIRETTA' con 'MANUALE' per rispettare il vincolo 'fatture_tipo_check'.
            cur.execute("""
                INSERT INTO fatture (numero, cliente_id, data, totale, stato_pagamento, data_pagamento, note, tipo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'MANUALE');
            """, (str(f["n_fattura"]), cliente_id, f["data"], f["importo"], f["stato"], f["data_pagamento"], f["note"]))
            
        conn.commit()
        print("✅ Storico importato con successo nel database!")
    except Exception as e:
        conn.rollback()
        print(f"❌ Errore durante l'importazione: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    importa()